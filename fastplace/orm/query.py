"""Fluent async query builder — the Fastplace ORM public query API.

``await Task.query().where(...).order_by(...).paginate(20)`` — every filter,
sort, and aggregation is pushed down to the database as SQL (never in-memory).
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import joinedload, selectinload, with_loader_criteria
from sqlalchemy.sql import Select

from fastplace.orm.pagination import Paginator
from fastplace.orm.scopes import is_scope
from fastplace.orm.session import ambient, run_read

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
        self._without_scopes: set[str] = set()

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

    def without_global_scope(self, name: str) -> QueryBuilder:
        """Drop one global scope for this query (``soft_delete`` included)."""
        self._without_scopes.add(name)
        return self

    def without_global_scopes(self) -> QueryBuilder:
        """Drop every global scope for this query."""
        self._without_scopes.update(self.model._resolved_global_scopes())
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
    def _global_scope_criteria(self) -> list[Any]:
        """Every registered global scope's criteria, minus removed ones.

        Soft delete rides the same registry; its two special modes
        (``with_deleted()`` bypasses it, ``only_deleted()`` inverts it) are
        the accessors the core scope is wired to.
        """
        criteria: list[Any] = []
        deleted_at = getattr(self.model, "deleted_at", None)
        for name, scope_ in self.model._resolved_global_scopes().items():
            if name == "soft_delete":
                # only_deleted() is an explicit mode choice — it wins over the
                # soft-delete escape hatch instead of widening to every row.
                if self._soft_delete_mode == ONLY_DELETED:
                    if deleted_at is not None:
                        criteria.append(deleted_at.is_not(None))
                    continue
                if (
                    self._soft_delete_mode == INCLUDE_DELETED
                    or "soft_delete" in self._without_scopes
                ):
                    continue
            if name in self._without_scopes:
                continue
            criteria.extend(scope_.criteria(self.model))
        return criteria

    def _core(self) -> Select:
        stmt = select(self.model)
        criteria = self._global_scope_criteria() + list(self._wheres)
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
                        f"{self.model.__name__} has no relationship '{part}' (path: {path})"
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
            # The path's terminal model carries its own global scopes — the
            # eager load must filter by them, exactly like relation() does.
            criteria = self._loader_criteria(current_mapper.class_)
            if criteria is not None:
                loaders.append(with_loader_criteria(current_mapper.class_, criteria))
        return loaders

    def _loader_criteria(self, target: type) -> Any | None:
        """Criteria for a relationship target's eager loads, or None.

        Evaluated at statement-build time (the context a contextvar-backed
        scope reads — a request's company, say — is bound by then, and the
        statement executes within the same await). The builder's own
        soft-delete mode and scope escapes propagate to the loaded
        relationship, mirroring :meth:`_global_scope_criteria`.
        """
        scopes = target._resolved_global_scopes()  # type: ignore[attr-defined]
        clauses: list[Any] = []
        deleted_at = getattr(target, "deleted_at", None)
        for name, scope_ in scopes.items():
            if name == "soft_delete":
                if deleted_at is None:
                    continue
                if self._soft_delete_mode == ONLY_DELETED:
                    clauses.append(deleted_at.is_not(None))
                elif (
                    self._soft_delete_mode == INCLUDE_DELETED
                    or "soft_delete" in self._without_scopes
                ):
                    continue
                else:
                    clauses.append(deleted_at.is_(None))
                continue
            if name in self._without_scopes:
                continue
            clauses.extend(scope_.criteria(target))
        if not clauses:
            return None
        from sqlalchemy import and_

        return and_(*clauses)

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

    # -- iteration ---------------------------------------------------------------
    def chunk(self, size: int) -> AsyncIterator[Any]:
        """Page through the query ``size`` rows at a time (OFFSET paging).

        ``async for row in Task.query().order_by(Task.id).chunk(500):`` — the
        memory ceiling is one page, not the table. Ordering is the caller's
        duty: without a stable ORDER BY, page boundaries are undefined, and
        under concurrent writes rows shift across offsets — skipped or
        delivered twice. :meth:`chunk_by_id` is the recommended default for
        exactly that reason. Pre-set limit/offset are overridden; chunking
        owns the paging window.
        """
        if size <= 0:
            raise ValueError("chunk size must be a positive integer")
        return self._chunk(size)

    async def _chunk(self, size: int) -> AsyncIterator[Any]:
        offset = 0
        while True:
            stmt = self._statement().limit(size).offset(offset)
            rows = list((await run_read(stmt)).scalars().all())
            if not rows:
                return
            for row in rows:
                yield row
            if len(rows) < size:
                return
            offset += size

    def chunk_by_id(self, size: int) -> AsyncIterator[Any]:
        """Keyset paging on the integer primary key — stable under writes.

        Each page resumes after the last key seen (``WHERE pk > last``), so a
        row inserted mid-iteration shows up in a later page instead of
        shifting every offset — offset paging's skipped/duplicated failure
        mode. The recommended iteration primitive whenever the model has a
        single integer primary key; TypeError otherwise. Statement ordering
        is always the primary key ascending — caller-set ORDER BY is not
        carried over, because keyset paging is only sound when the sort key
        IS the pagination key.
        """
        if size <= 0:
            raise ValueError("chunk size must be a positive integer")
        mapper = sa_inspect(self.model)
        pk_columns = mapper.primary_key
        if len(pk_columns) != 1:
            raise TypeError(
                f"{self.model.__name__} needs a single-column primary key for chunk_by_id()"
            )
        if pk_columns[0].type.python_type is not int:
            raise TypeError(
                f"{self.model.__name__}'s primary key is not an integer — keyset "
                "paging compares keys, so it rides an integer column"
            )
        return self._chunk_by_id(size, getattr(self.model, pk_columns[0].name))

    async def _chunk_by_id(self, size: int, pk_attr: Any) -> AsyncIterator[Any]:
        last: Any = None
        while True:
            stmt = select(self.model).where(*self._global_scope_criteria(), *self._wheres)
            if last is not None:
                stmt = stmt.where(pk_attr > last)
            stmt = stmt.order_by(pk_attr).limit(size)
            if self._eager:
                stmt = stmt.options(*self._loaders())
            rows = list((await run_read(stmt)).scalars().all())
            if not rows:
                return
            for row in rows:
                yield row
            last = getattr(rows[-1], pk_attr.key)
            if len(rows) < size:
                return

    async def cursor(self) -> AsyncIterator[Any]:
        """Stream rows one at a time (server-side cursor where the driver has one).

        ``stream_results`` asks the driver for cursor streaming: PostgreSQL
        (asyncpg) honors it and the process never materializes the whole
        result; SQLite (aiosqlite) has no server-side cursor and buffers the
        full result — the call still works, just without the memory win.
        Joins the ambient transaction, so the current scope's uncommitted
        writes stream back. Standalone (no open scope), the cursor opens its
        own read session and owns it outright — breaking out early closes it
        from wherever the generator is finalized. Keep the iteration short:
        the connection is held until the generator finishes.
        """
        stmt = self._statement().execution_options(stream_results=True)
        state = ambient()
        if state is not None:
            result = await state.session.stream(stmt)
            async for row in result.scalars():
                yield row
            return
        from fastplace.orm.manager import get_manager
        from fastplace.orm.session import reads_pinned_to_primary

        manager = get_manager()
        session = manager.session() if reads_pinned_to_primary() else manager.read_session()
        # Standalone: own the session outright — never bind_session(). A
        # ContextVar token held across the yield is reset in whichever task
        # runs aclose() (the GC may finalize the generator in any context),
        # where the reset crashes — and the crash skips session.close(),
        # leaking the connection. A plain close() in finally is
        # context-free. This stream is read-only: nothing to commit, and
        # close() discards the (empty) transaction either way.
        try:
            result = await session.stream(stmt)
            async for row in result.scalars():
                yield row
        finally:
            await session.close()
