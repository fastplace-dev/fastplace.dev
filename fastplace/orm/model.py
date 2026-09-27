"""Model base class — the Fastplace ORM public API surface.

Entities are declared with plain Python types and ``Field`` metadata; the
metaclass machinery rewrites annotations into SQLAlchemy 2.0 ``Mapped[...]``
columns underneath. Application code never touches SQLAlchemy directly
(escape hatches excepted — blueprint §8).
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Integer, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import Select

from fastplace.errors import SearchCapabilityMissing
from fastplace.orm.events import EventRegistry, fire
from fastplace.orm.fields import (
    _MISSING,
    Field,
    is_mapped_annotation,
    python_type_to_sa,
    resolve_annotation,
)
from fastplace.orm.query import QueryBuilder
from fastplace.orm.relationships import MorphMany, MorphOne, MorphTo, RelationshipMarker
from fastplace.orm.scopes import SoftDeleteScope, scope
from fastplace.orm.types import PortableDateTime

_MISSING_ANNOTATION = object()


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _snake(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if (
            ch.isupper()
            and i > 0
            and (not name[i - 1].isupper() or (i + 1 < len(name) and name[i + 1].islower()))
        ):
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _plural(word: str) -> str:
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


class _ClassProperty:
    """Attribute-style access on the class (``Model.sa_model``) on Py3.13+,
    where the ``@classmethod @property`` chaining was removed."""

    def __init__(self, fn: Any) -> None:
        self.fn = fn
        self.__doc__ = getattr(fn, "__doc__", None)

    def __get__(self, obj: Any, owner: Any = None) -> Any:
        return self.fn(owner if owner is not None else type(obj))


class Model(AsyncAttrs, DeclarativeBase):
    """Base model: annotation-driven columns, timestamps, soft deletes, events."""

    # The core global scope every model carries (blueprint §8 Query Scopes:
    # "soft-delete — Global, automatic (core)"). Packages add their own via
    # add_global_scope(); subclasses can narrow the set with __global_scopes__.
    __global_scopes__: dict[str, Any] = {"soft_delete": SoftDeleteScope()}

    # Redefining registry-level dunders for clarity; SQLAlchemy fills the rest.
    def __init_subclass__(cls, **kwargs: Any) -> None:
        abstract = cls.__dict__.get("__abstract__", False)
        if not abstract:
            _validate_dispatches(cls)
            _transform_declarative_fields(cls)
            _replace_superseded_declaration(cls)
        super().__init_subclass__(**kwargs)

    # ------------------------------------------------------------------
    # global scopes (extensible; the tenancy scope plugs in here)
    # ------------------------------------------------------------------
    @classmethod
    def _resolved_global_scopes(cls) -> dict[str, Any]:
        """MRO-merged scope registry — nearest class in the chain wins a name.

        Each class's ``__global_scopes__`` holds only its OWN entries (a
        ``None`` value is a tombstone: "removed at this level"), so the merge
        recomputes inheritance at read time and base-level add/remove/replace
        always reaches subclasses.
        """
        merged: dict[str, Any] = {}
        for klass in reversed(cls.__mro__):
            own = klass.__dict__.get("__global_scopes__")
            if own is None:
                continue
            for name, scope_ in own.items():
                if scope_ is None:
                    merged.pop(name, None)  # tombstone shadows any base entry
                else:
                    merged[name] = scope_
        return merged

    @classmethod
    def add_global_scope(cls, name: str, scope: Any) -> None:
        """Register an automatic filter for every query on this model.

        Scoped to ``cls`` only — registering on a subclass never leaks to the
        parent or to sibling models.
        """
        if name == "soft_delete":
            raise ValueError(
                "'soft_delete' is reserved for the core scope behind with_deleted()/only_deleted()"
            )
        # Write only the own entry — never snapshot inherited scopes into the
        # subclass, which would freeze them against later base-level changes.
        own = dict(cls.__dict__.get("__global_scopes__") or {})
        own[name] = scope
        cls.__global_scopes__ = own

    @classmethod
    def remove_global_scope(cls, name: str) -> None:
        """Drop a scope for this class — including one inherited from a base.

        Writes a tombstone in the class's own dict so the MRO merge cannot
        resurrect the base's entry.
        """
        if name == "soft_delete":
            raise ValueError(
                "'soft_delete' is the core scope behind with_deleted()/only_deleted() — "
                "escape it per query with without_global_scope('soft_delete') instead"
            )
        own = dict(cls.__dict__.get("__global_scopes__") or {})
        own[name] = None
        cls.__global_scopes__ = own

    # ------------------------------------------------------------------
    # declaration support
    # ------------------------------------------------------------------
    @classmethod
    def _default_tablename(cls) -> str:
        return _plural(_snake(cls.__name__))

    # ------------------------------------------------------------------
    # query entry points
    # ------------------------------------------------------------------
    @classmethod
    def query(cls) -> QueryBuilder:
        return QueryBuilder(cls)

    @classmethod
    def where(cls, *criteria: Any) -> QueryBuilder:
        return cls.query().where(*criteria)

    @classmethod
    def with_(cls, *relations: str) -> QueryBuilder:
        return cls.query().with_(*relations)

    @classmethod
    def with_deleted(cls) -> QueryBuilder:
        return cls.query().with_deleted()

    @classmethod
    def only_deleted(cls) -> QueryBuilder:
        return cls.query().only_deleted()

    @classmethod
    def without_global_scope(cls, name: str) -> QueryBuilder:
        return cls.query().without_global_scope(name)

    @classmethod
    def without_global_scopes(cls) -> QueryBuilder:
        return cls.query().without_global_scopes()

    @classmethod
    async def all(cls) -> list[Any]:
        return await cls.query().get()

    @classmethod
    async def get(cls) -> list[Any]:
        return await cls.query().get()

    @classmethod
    async def first(cls) -> Any | None:
        return await cls.query().first()

    @classmethod
    async def count(cls) -> int:
        return await cls.query().count()

    @classmethod
    async def find(cls, pk: Any) -> Any | None:
        pk_attr = cls._pk_attr()
        return await cls.query().where(pk_attr == pk).first()

    @classmethod
    async def find_or_fail(cls, pk: Any) -> Any:
        from fastplace.errors import NotFoundError

        found = await cls.find(pk)
        if found is None:
            raise NotFoundError(f"{cls.__name__} #{pk} not found")
        return found

    # ------------------------------------------------------------------
    # core conventional scopes (blueprint Query Scopes table)
    # ------------------------------------------------------------------
    @scope
    def is_active(cls, query=None):  # noqa: N805 — scope descriptor receives cls
        """Boolean activity filter over the ``active`` column (core scope)."""
        if query is None:
            query = cls.query()
        if "active" not in cls.__table__.columns:
            raise AttributeError(
                f"{cls.__name__} has no 'active' column; is_active() only "
                "applies to models declaring one"
            )
        return query.where(cls.active.is_(True))

    @scope
    def authorization(cls, query=None, actor_id=None, *, column: str = "user_id"):  # noqa: N805
        """Ownership / membership filter (core scope)."""
        if query is not None and not isinstance(query, QueryBuilder):
            # Class-level call form: Model.authorization(actor_id).
            query, actor_id = None, query
        if actor_id is None:
            raise TypeError("authorization() requires the owning actor id")
        if query is None:
            query = cls.query()
        if column not in cls.__table__.columns:
            raise AttributeError(
                f"{cls.__name__} has no {column!r} column; pass column= for a "
                "different ownership column"
            )
        return query.where(getattr(cls, column) == actor_id)

    # ------------------------------------------------------------------
    # mass assignment (OWASP: guarded attributes never set from payloads)
    # ------------------------------------------------------------------
    _ALWAYS_GUARDED = ("created_at", "updated_at", "deleted_at")

    @classmethod
    def _mass_assignable(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Filter a mass-assignment payload against the model's guard rules.

        ``__fillable__`` (allowlist — non-listed keys are ignored and ``__guarded__`` (extra denylist) customize the surface.
        The primary key and audit columns always raise; direct attribute
        assignment remains the escape hatch.
        """
        from fastplace.errors import MassAssignmentError

        pk_names = {column.name for column in inspect(cls).primary_key}
        fillable = getattr(cls, "__fillable__", None)
        # The audit/tombstone columns are never mass-assignable; the primary
        # key is guarded unless the model lists it in __fillable__ (natural
        # keys opt in explicitly).
        blocked = (
            set(cls._ALWAYS_GUARDED)
            | set(getattr(cls, "__guarded__", ()))
            | (pk_names - set(fillable or ()))
        )

        filtered: dict[str, Any] = {}
        for key, value in values.items():
            if fillable is not None and key not in fillable:
                continue
            if key in blocked:
                raise MassAssignmentError(f"{cls.__name__}.{key} is guarded from mass assignment")
            filtered[key] = value
        return filtered

    @classmethod
    async def create(cls, **values: Any) -> Any:
        instance = cls(**cls._mass_assignable(values))
        await instance.save()
        return instance

    @classmethod
    def _pk_attr(cls) -> Any:
        return getattr(cls, inspect(cls).primary_key[0].name)

    # ------------------------------------------------------------------
    # capability-gated search
    # ------------------------------------------------------------------
    @classmethod
    async def vector_search(cls, embedding: list[float], limit: int = 10) -> list[Any]:
        """Similarity search; requires a vector-capable backend (pgvector)."""
        from fastplace.db import db

        if not db.capabilities.supports_vector:
            raise SearchCapabilityMissing(
                "vector search requires a vector-capable backend (PostgreSQL + pgvector)"
            )
        column = cls._vector_column()
        stmt = (
            select(cls)
            .where(*cls._search_scope_criteria())
            .order_by(column.cosine_distance(embedding))
            .limit(limit)
        )
        from fastplace.orm.session import run_read

        result = await run_read(stmt)
        return list(result.scalars().all())

    @classmethod
    async def full_text_search(cls, query: str, limit: int = 10) -> list[Any]:
        """Native full-text search; requires a full-text-capable backend."""
        from fastplace.db import db

        if not db.capabilities.supports_full_text:
            raise SearchCapabilityMissing(
                "full-text search requires a full-text-capable backend (PostgreSQL FTS by default)"
            )
        from sqlalchemy import String, Text

        text_columns = [
            column for column in cls.__table__.columns if isinstance(column.type, (String, Text))
        ]
        if not text_columns:
            raise SearchCapabilityMissing(f"{cls.__name__} has no text columns to search")
        vector = func.to_tsvector("english", func.concat_ws(" ", *text_columns))
        stmt = (
            select(cls)
            .where(*cls._search_scope_criteria())
            .where(vector.op("@@")(func.plainto_tsquery("english", query)))
            .limit(limit)
        )
        from fastplace.orm.session import run_read

        result = await run_read(stmt)
        return list(result.scalars().all())

    @classmethod
    def _not_deleted(cls) -> Any:
        """Soft-delete predicate (kept for callers outside the search paths)."""
        deleted_at = getattr(cls, "deleted_at", None)
        return deleted_at.is_(None) if deleted_at is not None else None

    @classmethod
    def _search_scope_criteria(cls) -> list[Any]:
        """Global-scope criteria for the capability-gated searches.

        vector_search()/full_text_search() must ride the same scope engine as
        every other read — a scope a search bypasses (tenancy isolation, an
        archived filter) is data leakage through a side door.
        """
        return cls.query()._global_scope_criteria()

    @classmethod
    def _vector_column(cls) -> Any:
        for column in cls.__table__.columns:
            # pgvector >= 0.4 renamed the class Vector -> VECTOR; duck-type on
            # the distance operator so both register.
            if type(column.type).__name__ in ("Vector", "VECTOR") or hasattr(
                column.type, "cosine_distance"
            ):
                return column
        raise SearchCapabilityMissing(f"{cls.__name__} has no VectorField column")

    # ------------------------------------------------------------------
    # escape hatches (blueprint §8 — framework-coupled by design)
    # ------------------------------------------------------------------
    @_ClassProperty
    def sa_model(cls: type[Any]) -> type[Any]:
        """The underlying SQLAlchemy mapped class."""
        return cls

    @classmethod
    def sa_query(cls) -> Select:
        """A raw SQLAlchemy select() statement for advanced work."""
        return select(cls)

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    @classmethod
    def on(cls, event: str, handler: Any) -> None:
        registry = cls.__dict__.get("_fastplace_events")
        if registry is None:
            registry = EventRegistry()
            cls._fastplace_events = registry  # type: ignore[attr-defined]
        registry.on(event, handler)

    # ------------------------------------------------------------------
    # instance persistence
    # ------------------------------------------------------------------
    async def save(self) -> Model:
        state = inspect(self)
        is_new = state.transient or state.pending
        await fire(self, "creating" if is_new else "updating")
        await self._persist()
        await fire(self, "created" if is_new else "updated")
        return self

    async def update(self, **values: Any) -> Model:
        for key, value in type(self)._mass_assignable(values).items():
            setattr(self, key, value)
        return await self.save()

    async def delete(self) -> Model:
        """Soft-delete: stamp ``deleted_at`` and hide from default queries."""
        await fire(self, "deleting")
        self.deleted_at = _utcnow()
        await self._persist()
        await fire(self, "deleted")
        return self

    async def force_delete(self) -> None:
        """Hard-delete the row from the database."""
        await fire(self, "deleting")

        pk_name = inspect(type(self)).primary_key[0].name
        pk_attr = type(self)._pk_attr()

        from sqlalchemy import delete

        async def action(session: Any) -> None:
            await session.execute(delete(type(self)).where(pk_attr == getattr(self, pk_name)))

        from fastplace.orm.session import run_write

        await run_write(action)
        await fire(self, "deleted")

    async def restore(self) -> Model:
        """Restore a soft-deleted record."""
        self.deleted_at = None  # type: ignore[assignment]
        await self._persist()
        await fire(self, "restored")
        return self

    async def refresh(self) -> Model:
        """Reload column values from the database.

        Soft-deleted rows are still reachable (the tombstone is the row's
        state, not a reason to pretend it vanished); a row that no longer
        exists raises instead of silently no-oping. Global scopes are bypassed
        entirely — the row is already in hand, and a scope that started hiding
        it mid-request must not turn a reload into NotFoundError.
        """
        from sqlalchemy.orm.attributes import set_committed_value

        from fastplace.errors import NotFoundError

        model = type(self)
        pk_name = inspect(model).primary_key[0].name
        fresh = (
            await model.query()
            .without_global_scopes()
            .where(model._pk_attr() == getattr(self, pk_name))
            .first()
        )
        if fresh is None:
            raise NotFoundError(f"{model.__name__} #{getattr(self, pk_name)} not found")
        for column in model.__table__.columns:
            if column.key in fresh.__dict__:
                # Set as committed — discards pending local edits instead
                # of flagging the instance dirty.
                set_committed_value(self, column.key, getattr(fresh, column.key))
        return self

    async def relation(self, name: str) -> Any:
        """Load a relationship with an explicit SELECT: ``await user.relation("posts")``.

        Relationships are declared ``lazy="raise"``, so this (or eager loading
        via ``.with_(...)``) is the supported way to load them — accidental
        sync access fails loudly instead of doing blocking IO under asyncio.
        """
        from sqlalchemy import select
        from sqlalchemy.orm import with_parent
        from sqlalchemy.orm.attributes import set_committed_value

        from fastplace.orm.query import QueryBuilder
        from fastplace.orm.session import run_read

        prop = inspect(type(self)).relationships[name]
        target = prop.mapper.class_
        # with_parent accepts a RelationshipProperty at runtime; the stubs
        # only declare the QueryableAttribute overload.
        stmt = select(target).where(with_parent(self, prop))  # type: ignore[arg-type]
        # The target's global scopes ride along — a child hidden from every
        # direct query (soft-deleted, archived, another tenant) must not come
        # back through the relationship door.
        criteria = QueryBuilder(target)._global_scope_criteria()
        if criteria:
            stmt = stmt.where(*criteria)
        result = await run_read(stmt)
        if prop.uselist:
            loaded = list(result.scalars().all())
        else:
            loaded = result.scalars().first()
        # Populate the attribute as committed so follow-up access works
        # without a second query. NOTE: morph relations are viewonly —
        # appending to the loaded collection is silently dropped by design;
        # create the child with its type + id pair instead (see
        # fastplace.orm.relationships).
        set_committed_value(self, name, loaded)
        return loaded

    async def morph_to(self) -> Any | None:
        """Resolve a polymorphic parent: ``await comment.morph_to()``.

        The declaring class names the column pair through a ``morph_to()``
        marker; the framework's morph map (``morph_map({type: Model})``)
        resolves the stored type string to the parent model.
        """
        from fastplace.orm.relationships import morph_map

        config = getattr(type(self), "_fastplace_morph", None)
        if config is None:
            raise AttributeError(
                f"{type(self).__name__} declares no morph_to() marker — add "
                "`parent = morph_to(<type_field>, <id_field>)` to the model"
            )
        type_field, id_field = config
        morph_type = getattr(self, type_field, None)
        morph_id = getattr(self, id_field, None)
        if morph_type is None or morph_id is None:
            # An unpaired row (saved before its parent, or genuinely parentless)
            # points nowhere — resolve to None rather than a type-registry error.
            return None
        mapping = morph_map()
        if morph_type not in mapping:
            raise ValueError(
                f"unknown morph type {morph_type!r} — register it: "
                f"morph_map({{{morph_type!r}: <Model>}})"
            )
        return await mapping[morph_type].find(morph_id)

    # -- internals --------------------------------------------------------------
    async def _persist(self) -> None:
        async def action(session: Any) -> None:
            session.add(self)
            await session.flush()

        from fastplace.orm.session import run_write

        await run_write(action)

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        hidden = set(getattr(type(self), "__hidden__", ()))
        data: dict[str, Any] = {}
        for column in type(self).__table__.columns:
            if column.key in hidden or column.key not in self.__dict__:
                continue
            data[column.key] = _json_safe(getattr(self, column.key))
        return data

    def to_dto(self, dto_class: Any) -> Any:
        """Validate this record against a Pydantic DTO (from_attributes models)."""
        return dto_class.model_validate(self, from_attributes=True)

    def __repr__(self) -> str:  # pragma: no cover — debug convenience
        pk_name = "id"
        try:
            pk_name = inspect(type(self)).primary_key[0].name
        except Exception:
            pass
        return f"<{type(self).__name__} {pk_name}={getattr(self, pk_name, None)!r}>"


def _json_safe(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


# ---------------------------------------------------------------------------
# declarative field transformation
# ---------------------------------------------------------------------------


def _transform_declarative_fields(cls: type) -> None:
    """Rewrite Fastplace-style annotations into SQLAlchemy Mapped columns."""
    import sys

    own_annotations = dict(cls.__dict__.get("__annotations__") or {})
    globalns = getattr(sys.modules.get(cls.__module__), "__dict__", {})
    from fastplace.config import config

    url = str(config("DATABASE_URL", default="sqlite+aiosqlite:///./database.sqlite3"))

    # Columns declared on __abstract__ bases are inherited (subclass wins).
    merged_annotations: dict[str, Any] = {}
    inherited_defaults: dict[str, Any] = {}
    for base in reversed(cls.__mro__[1:]):
        if base is Model or not (isinstance(base, type) and issubclass(base, Model)):
            continue
        if not vars(base).get("__abstract__", False):
            continue  # concrete-base inheritance is SQLAlchemy's domain
        for name, raw_annotation in (vars(base).get("__annotations__") or {}).items():
            if name.startswith("_") or name in own_annotations:
                continue
            merged_annotations[name] = raw_annotation
            if name in vars(base):
                inherited_defaults[name] = vars(base)[name]
    merged_annotations.update(own_annotations)

    resolved_annotations: dict[str, Any] = {}
    has_primary_key = False
    declared_pk_name: str | None = None
    relationship_markers: list[tuple[str, RelationshipMarker | MorphTo]] = []

    def value_for(name: str) -> Any:
        if name in cls.__dict__:
            return cls.__dict__[name]
        return inherited_defaults.get(name, _MISSING)

    # Pass 1 — columns (relationships are deferred so the primary key and the
    # self-referential FK attributes are known when they are built).
    for name, raw_annotation in merged_annotations.items():
        if name.startswith("_"):
            continue
        value = value_for(name)
        annotation = resolve_annotation(raw_annotation, globalns)

        if is_mapped_annotation(annotation):
            # Developer used Mapped[...] explicitly — pass through untouched,
            # but still honor an escape-hatch primary key declaration.
            column = getattr(value, "column", None)
            if getattr(column, "primary_key", False):
                has_primary_key = True
                declared_pk_name = name
            resolved_annotations[name] = annotation
            continue

        if isinstance(value, (RelationshipMarker, MorphTo)):
            relationship_markers.append((name, value))
            continue

        if isinstance(annotation, str):
            raise ConfigurationError_cls(
                cls,
                name,
                f"annotation {annotation!r} could not be resolved; relationships "
                "must use has_many/has_one/belongs_to/many_to_many markers",
            )

        field = value if isinstance(value, Field) else Field()
        plain_default = (
            value if not isinstance(value, Field) and value is not _MISSING else _MISSING
        )

        column_type = python_type_to_sa(annotation, field, url)

        kwargs: dict[str, Any] = {}
        if field.primary_key:
            kwargs["primary_key"] = True
            has_primary_key = True
            declared_pk_name = name
        if field.has_default():
            if field.default_factory is not _MISSING:
                kwargs["default"] = field.default_factory
            else:
                kwargs["default"] = field.default
        elif plain_default is not _MISSING:
            kwargs["default"] = plain_default
        if field.index:
            kwargs["index"] = True
        if field.unique:
            kwargs["unique"] = True
        if field.server_default is not None:
            kwargs["server_default"] = field.server_default
        if field.autoincrement is not None:
            kwargs["autoincrement"] = field.autoincrement

        from typing import Union
        from typing import get_args as _get_args
        from typing import get_origin as _get_origin

        is_optional = (
            (_get_origin(annotation) is Union and type(None) in _get_args(annotation))
            or "| None" in str(raw_annotation)
            or "Optional[" in str(raw_annotation)
        )
        kwargs.setdefault(
            "nullable",
            is_optional if is_optional else (False if field.nullable is None else field.nullable),
        )

        # ForeignKey is positional on Column/mapped_column — a `foreign_key=`
        # kwarg would be silently ignored (only a dialect warning).
        args: tuple[Any, ...] = (
            (column_type, ForeignKey(field.foreign_key)) if field.foreign_key else (column_type,)
        )
        setattr(cls, name, mapped_column(*args, **kwargs))

        _mapped: Any = Mapped
        resolved_annotations[name] = _mapped[annotation]

    # Auto id + timestamps when not declared.
    if not has_primary_key and "id" not in resolved_annotations:
        cls.id = mapped_column(Integer, primary_key=True, autoincrement=True)  # type: ignore[attr-defined]
        resolved_annotations["id"] = Mapped[int]
        declared_pk_name = "id"
    pk_name = declared_pk_name or "id"

    stamp_specs: list[tuple[str, dict[str, Any]]] = [
        ("created_at", {"default": _utcnow}),
        ("updated_at", {"default": _utcnow, "onupdate": _utcnow}),
        ("deleted_at", {}),
    ]
    for stamp_name, column_kwargs in stamp_specs:
        if stamp_name in merged_annotations or stamp_name in resolved_annotations:
            continue

        setattr(
            cls,
            stamp_name,
            mapped_column(PortableDateTime(), nullable=True, **column_kwargs),
        )
        resolved_annotations[stamp_name] = Mapped[datetime.datetime | None]

    # Pass 2 — relationships: annotated markers plus any marker assigned
    # without an annotation (silently dropping them hides real bugs).
    collected = {name for name, _ in relationship_markers}
    for name, value in list(vars(cls).items()):
        if name.startswith("_") or name in collected or name in resolved_annotations:
            continue
        if isinstance(value, (RelationshipMarker, MorphTo)):
            relationship_markers.append((name, value))

    tablename = cls.__dict__.get("__tablename__") or _plural(_snake(cls.__name__))
    for name, marker in relationship_markers:
        # morph_to has no static target, so no relationship is built — record
        # the column pair for the instance-level `await row.morph_to()`.
        if isinstance(marker, MorphTo):
            cls._fastplace_morph = (marker.type_field, marker.id_field)  # type: ignore[attr-defined]
            if name in vars(cls):
                delattr(cls, name)
            continue

        extras: dict[str, Any] = dict(marker.extra or {})
        if isinstance(marker, (MorphMany, MorphOne)):
            # The join needs to know its owner side; the default type string
            # is the owner's table name. The owner's pk column is passed too —
            # owners are not guaranteed an ``id`` primary key.
            extras.setdefault("_morph_owner", cls.__name__)
            extras.setdefault("_morph_type_name", tablename)
            extras.setdefault("_morph_pk", pk_name)
        if marker.target == cls.__name__:
            # Self-referential adjacency lists: remote_side lives on the
            # many-to-one side (the parent), pointing at the primary key.
            if marker.__class__.__name__ in ("BelongsTo", "HasOne"):
                extras.setdefault("remote_side", f"{cls.__name__}.{pk_name}")
            elif marker.backref and "backref" not in extras and "remote_side" not in extras:
                from sqlalchemy.orm import backref as orm_backref

                extras["backref"] = orm_backref(
                    marker.backref, remote_side=f"{cls.__name__}.{pk_name}"
                )
        setattr(cls, name, marker.build(**extras))
        kind_many = marker.__class__.__name__ in ("HasMany", "ManyToMany", "MorphMany")
        _mapped_rel: Any = Mapped
        resolved_annotations[name] = _mapped_rel[list[Any]] if kind_many else _mapped_rel[Any]

    cls.__annotations__ = resolved_annotations
    cls.__tablename__ = tablename  # type: ignore[attr-defined]


def _replace_superseded_declaration(cls: type) -> None:
    """A redeclared model replaces its predecessor instead of colliding.

    Re-executing a model module (in-process project boots, importer
    evictions) redefines declarative classes whose tables are still on the
    shared metadata. SQLAlchemy refuses the Table outright and leaves the
    stale class in the string-lookup registry, where it surfaces later as
    ``Multiple classes found for path`` ambiguity. The superseded
    declaration is therefore disposed explicitly — its table leaves the
    metadata, its mapper and registry entries are released — and the new
    definition owns the name outright.
    """
    tablename = cls.__dict__.get("__tablename__")
    if tablename is None:
        return
    if tablename in Model.metadata.tables:
        Model.metadata.remove(Model.metadata.tables[tablename])
    stale: list[type] = []
    stack = [Model]
    while stack:
        for subclass in stack.pop().__subclasses__():
            stack.append(subclass)
            if (
                subclass is not cls
                and subclass.__module__ == cls.__module__
                and getattr(subclass, "__tablename__", None) == tablename
            ):
                stale.append(subclass)
    registry = Model.registry
    for other in stale:
        manager = getattr(other, "_sa_class_manager", None)
        if manager is not None:
            # The per-class teardown that registry.dispose() gives every
            # class at once: dispose flags on the mapper, the registry entry
            # and instrumentation, and the manager itself — a disposed
            # manager left in registry._managers breaks the next
            # configure_mappers() with a None mapper.
            registry._dispose_manager_and_mapper(manager)  # noqa: SLF001
            registry._managers.pop(manager, None)  # noqa: SLF001
        else:  # pragma: no cover — an uninstrumented subclass has no manager
            registry._dispose_cls(other)  # noqa: SLF001


def ConfigurationError_cls(cls: type, attr: str, detail: str) -> Exception:
    from fastplace.errors import ConfigurationError

    return ConfigurationError(f"{cls.__module__}.{cls.__name__}.{attr}: {detail}")


def _validate_dispatches(cls: type) -> None:
    """``__dispatches__`` maps lifecycle events to domain events for the queue.

    Only post-flush lifecycle events can map to queue work — the payload
    carries the primary key, which pre-flush events ("creating", …) cannot
    supply. Unknown names (typos) fail here, at class definition, instead of
    silently never firing.
    """
    dispatches = cls.__dict__.get("__dispatches__")
    if not dispatches:
        return
    from fastplace.orm.events import EVENTS

    queueable = {"created", "updated", "deleted", "restored"}
    unknown = sorted(set(dispatches) - set(EVENTS))
    if unknown:
        raise ValueError(
            f"{cls.__name__}.__dispatches__ names unknown lifecycle events: "
            f"{', '.join(unknown)} — known events: {', '.join(sorted(EVENTS))}"
        )
    pre_flush = sorted(set(dispatches) - queueable)
    if pre_flush:
        raise ValueError(
            f"{cls.__name__}.__dispatches__ maps pre-flush events ({', '.join(pre_flush)}) "
            "whose payloads cannot carry the primary key yet — map post-flush "
            "events (created/updated/deleted/restored) instead; in-process "
            "listeners via Model.on() have no such restriction"
        )
