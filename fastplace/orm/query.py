"""Fluent async query builder — the Fastplace ORM public query API.

``await Task.query().where(...).order_by(...).paginate(20)`` — every filter,
sort, and aggregation is pushed down to the database as SQL (never in-memory).
"""

from __future__ import annotations

import inspect
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.sql import Select

from fastplace.orm.pagination import Paginator
from fastplace.orm.scopes import is_scope
from fastplace.orm.session import run_read

EXCLUDE_DELETED = "exclude"
ONLY_DELETED = "only"
INCLUDE_DELETED = "include"


def _find_class_attr(cls: Any, name: str) -> Any:
    """Fetch a descriptor straight from the MRO's ``__dict__``s (no binding)."""
    for klass in getattr(cls, "__mro__", (cls,)):
        if name in vars(klass):
            return vars(klass)[name]
    return None


class QueryBuilder:
    """Builds and executes SQL for one model."""

    def __init__(self, model: Any) -> None:
        self.model = model
        self._wheres: list[Any] = []
        self._orders: list[Any] = []
        self._limit: int | None = None
        self._offset: int | None = None
        self._eager: list[str] = []
        self._soft_delete_mode = EXCLUDE_DELETED

    # -- chaining ---------------------------------------------------------------
    def where(self, *criteria: Any) -> QueryBuilder:
        for criterion in criteria:
            if criterion is not None:
                self._wheres.append(criterion)
        return self

    def order_by(self, *criteria: Any) -> QueryBuilder:
        self._orders.extend(criteria)
        return self

    def limit(self, value: int) -> QueryBuilder:
        self._limit = value
        return self

    def offset(self, value: int) -> QueryBuilder:
        self._offset = value
        return self

    def with_(self, *relations: str) -> QueryBuilder:
        """Eager-load relationships — prevents N+1 queries."""
        self._eager.extend(relations)
        return self

    def with_deleted(self) -> QueryBuilder:
        """Include soft-deleted rows."""
        self._soft_delete_mode = INCLUDE_DELETED
        return self

    def only_deleted(self) -> QueryBuilder:
        """Only soft-deleted rows."""
        self._soft_delete_mode = ONLY_DELETED
        return self

    def __getattr__(self, name: str):
        """Apply model scopes as chainable methods: ``.query().in_progress()``."""
        if name.startswith("_"):
            raise AttributeError(name)
        # Raw class-dict lookup (walking the MRO) — plain getattr() would
        # trigger the descriptor's __get__ and hand back a bound partial.
        raw = _find_class_attr(self.model, name)
        if raw is not None and is_scope(raw):
            def apply(*args: Any, **kwargs: Any) -> QueryBuilder:
                result = raw.fn(self.model, self, *args, **kwargs)
                return result if isinstance(result, QueryBuilder) else self

            apply.__name__ = name
            return apply
        # Blueprint declaration form: a plain ``@classmethod`` whose second
        # parameter is the query builder (``def in_progress(cls, query)``).
        if isinstance(raw, classmethod):
            fn = raw.__func__
            try:
                params = list(inspect.signature(fn).parameters)
            except (TypeError, ValueError):  # pragma: no cover — exotic builtins
                params = []
            if len(params) >= 2 and params[1] == "query":
                def apply_plain(*args: Any, **kwargs: Any) -> QueryBuilder:
                    result = fn(self.model, self, *args, **kwargs)
                    return result if isinstance(result, QueryBuilder) else self

                apply_plain.__name__ = name
                return apply_plain
        raise AttributeError(
            f"{getattr(self.model, '__name__', self.model)} has no query scope '{name}'"
        )

    # -- statement construction -------------------------------------------------
    def _soft_delete_criterion(self) -> list[Any]:
        deleted_at = getattr(self.model, "deleted_at", None)
        if deleted_at is None or self._soft_delete_mode == INCLUDE_DELETED:
            return []
        if self._soft_delete_mode == ONLY_DELETED:
            return [deleted_at.is_not(None)]
        return [deleted_at.is_(None)]

    def _core(self) -> Select:
        stmt = select(self.model)
        criteria = self._soft_delete_criterion() + list(self._wheres)
        if criteria:
            stmt = stmt.where(*criteria)
        if self._orders:
            stmt = stmt.order_by(*self._orders)
        return stmt

    def _statement(self) -> Select:
        stmt = self._core()
        if self._limit is not None:
            stmt = stmt.limit(self._limit)
        if self._offset is not None:
            stmt = stmt.offset(self._offset)
        if self._eager:
            stmt = stmt.options(*self._loaders())
        return stmt

    def _loaders(self) -> list[Any]:
        loaders: list[Any] = []
        mapper = self.model.__mapper__
        for path in self._eager:
            chain: Any = None
            current_mapper = mapper
            for part in path.split("."):
                if part not in current_mapper.relationships:
                    raise ValueError(
                        f"{self.model.__name__} has no relationship '{part}' "
                        f"(path: {path})"
                    )
                rel = current_mapper.relationships[part]
                attr = getattr(current_mapper.class_, part)
                one_to_many = rel.direction.name == "ONETOMANY" or rel.secondary is not None
                factory = selectinload if one_to_many else joinedload
                # Chain through Load.__getattr__ (public API — no link_to).
                chain = factory(attr) if chain is None else getattr(chain, factory.__name__)(attr)
                current_mapper = rel.mapper
            if chain is not None:
                loaders.append(chain)
        return loaders

    # -- execution ---------------------------------------------------------------
    async def get(self) -> list[Any]:
        result = await run_read(self._statement())
        return list(result.scalars().all())

    async def first(self) -> Any | None:
        stmt = self._statement().limit(1)
        result = await run_read(stmt)
        return result.scalars().first()

    async def find(self, pk: Any) -> Any | None:
        """Fetch one row by primary key, honoring chained scopes/eager loads."""
        return await self.where(self.model._pk_attr() == pk).first()

    async def find_or_fail(self, pk: Any) -> Any:
        from fastplace.errors import NotFoundError

        found = await self.find(pk)
        if found is None:
            raise NotFoundError(f"{self.model.__name__} #{pk} not found")
        return found

    async def count(self) -> int:
        stmt = select(func.count()).select_from(self._core().subquery())
        result = await run_read(stmt)
        return int(result.scalar_one())

    async def exists(self) -> bool:
        stmt = select(self._core().exists())
        result = await run_read(stmt)
        return bool(result.scalar_one())

    async def paginate(self, per_page: int = 20, page: int = 1) -> Paginator:
        page = max(1, page)
        total = await self.count()
        items = await self.limit(per_page).offset((page - 1) * per_page).get()
        return Paginator(items, total=total, per_page=per_page, current_page=page)
