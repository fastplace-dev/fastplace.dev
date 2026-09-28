"""Fastplace document adapter — MongoDB behind a Motor-style API (T6.3).

Deliberately separate from the relational ORM: no SQLAlchemy, no tables,
no migrations. ``Document`` subclasses map to collections and speak filter
documents natively — the idiomatic escape hatch is a raw Mongo filter, not
an emulation of SQL.

    from fastplace.orm.documents import Document

    class Article(Document):          # collection "articles"
        title: str = ""
        views: int = 0

    await Article.create(title="hello")
    await Article.where(title="hello").get()                      # equality kwargs
    await Article.where({"views": {"$gte": 3}}).sort("views", -1).limit(5).get()

Connection: ``MONGODB_URL`` (+ optional ``MONGODB_DATABASE`` when the URL
names no database) — resolved lazily through the config layer.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, ClassVar

try:  # The adapter degrades to a use-time error without the extra.
    from pymongo import ReturnDocument
    from pymongo.asynchronous.collection import AsyncCollection
    from pymongo.asynchronous.mongo_client import AsyncMongoClient
except ImportError:  # pragma: no cover — depends on extras installed
    ReturnDocument = None  # type: ignore[assignment,misc]
    AsyncCollection = Any  # type: ignore[assignment,misc]
    AsyncMongoClient = None  # type: ignore[assignment,misc]

#: Brought in lazily via ``fastplace.config`` so document users who never
#: touch MongoDB pay no import-time config read.
_client: Any | None = None
_database = None  # AsyncDatabase, kept untyped to avoid a second import


def _require_pymongo() -> None:
    """Raise the actionable error instead of an ImportError at import time.

    Sibling distributions (fastplace-tenancy) import this module from their
    own users' environments, which may not have the mongodb extra.
    """
    if AsyncMongoClient is None:
        from fastplace.errors import ConfigurationError

        raise ConfigurationError(
            "pymongo is not installed — the document adapter needs the "
            "mongodb extra (pip install 'fastplace[mongodb]')"
        )


#: Acronym-aware camel→snake: split only at lower/digit→Upper transitions
#: and Upper→UpperLower boundaries, so ``APIKey`` → ``api_key`` and
#: ``HTTPRequest`` → ``http_request`` (not ``a_p_i_key``).
_CAMEL = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-z0-9])(?=[A-Z])")
_DEFAULT_DATABASE = "fastplace"


def _pluralize(name: str) -> str:
    if name.endswith("s"):
        return name
    if name.endswith("y") and name[-2:-1] not in "aeiou":
        return f"{name[:-1]}ies"
    return f"{name}s"


def _database_name_for(url: str, configured: str | None) -> str:
    """Database from the URL path, else config, else the framework default."""
    path = url.split("://", 1)[-1].split("/", 1)
    if len(path) == 2 and path[1].strip("/"):
        return path[1].strip("/").split("?", 1)[0]
    return configured or _DEFAULT_DATABASE


def get_documents_client() -> Any:
    """Process-wide async client, built from ``MONGODB_URL`` on first use."""
    global _client
    if _client is None:
        _require_pymongo()
        from fastplace.config import config

        url = config("MONGODB_URL", default="mongodb://localhost:27017")
        _client = AsyncMongoClient(str(url))
    return _client


def documents_database():
    """The resolved ``AsyncDatabase`` for the configured URL."""
    global _database
    if _database is None:
        from fastplace.config import config

        url = str(config("MONGODB_URL", default="mongodb://localhost:27017"))
        configured = config("MONGODB_DATABASE", default=None)
        _database = get_documents_client()[_database_name_for(url, configured)]
    return _database


#: Strong references to fire-and-forget close tasks — without them the
#: running loop's only reference can be garbage-collected mid-close.
_closing_tasks: set[asyncio.Task[None]] = set()


async def aclose_documents() -> None:
    """Awaitable reset for async teardown — completes the client close."""
    global _client, _database
    client, _client, _database = _client, None, None
    if client is not None:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 — teardown must never raise
            pass


def reset_documents() -> None:
    """Drop the client singleton (tests, reconfiguration).

    Sync contexts cannot await a pymongo close (and blocking on topology
    shutdown costs seconds), so this drops the reference and — when a loop
    happens to be running — schedules the close as a strongly-referenced
    background task. Async callers should prefer ``await aclose_documents()``.
    Never raises.
    """
    global _client, _database
    client, _client, _database = _client, None, None
    if client is not None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop: nothing sync can close a pymongo client
        try:
            task = loop.create_task(client.aclose())
        except Exception:  # noqa: BLE001 — teardown must never raise
            return
        _closing_tasks.add(task)
        task.add_done_callback(_closing_tasks.discard)


_MISSING = object()


def _document_payload(doc_cls: type[Document], data: dict[str, Any]) -> dict[str, Any]:
    """Fill annotation defaults for keys the caller did not provide.

    An annotation declares a field; the class attribute is its default
    (a zero-arg callable is a factory, so ``tags: list = list`` yields a
    fresh ``[]`` per insert instead of one shared mutable list).
    """
    payload = dict(data)
    # Subclass-first MRO walk: the most-derived declaration of a field owns
    # its default, matching normal attribute shadowing. Caller-provided
    # data already sits in ``payload`` and wins over every default.
    for klass in doc_cls.__mro__:
        if klass in (Document, object):
            continue
        annotations = klass.__dict__.get("__annotations__", {})
        for key in annotations:
            if key.startswith("_") or key in payload:
                continue
            default = klass.__dict__.get(key, _MISSING)
            if default is _MISSING:
                continue
            payload[key] = default() if callable(default) else default
    return payload


class DocumentQuery:
    """Lazy, immutable MongoDB query — nothing hits the wire until an
    awaitable terminal method (``get``/``first``/``count``/``delete``)."""

    def __init__(
        self,
        doc_cls: type[Document],
        filter: dict[str, Any] | None = None,
        sort: list[tuple[str, int]] | None = None,
        skip: int = 0,
        limit: int | None = None,
    ) -> None:
        self._doc_cls = doc_cls
        self._filter = dict(filter or {})
        self._sort = list(sort or [])
        self._skip = skip
        self._limit = limit

    def where(self, filter: dict[str, Any] | None = None, /, **equality) -> DocumentQuery:
        """AND a raw Mongo filter and/or equality kwargs into the match.

        Constraints on the same field intersect through ``$and`` — a later
        ``where()`` must never silently replace an earlier one (that would
        drop security filters chained by callers).
        """
        merged = {**(filter or {}), **equality}
        combined = dict(self._filter)
        clauses = list(combined.pop("$and", []))
        for key, value in merged.items():
            if key == "$and":
                clauses.extend(value)
            elif key in combined and combined[key] != value:
                clauses.append({key: combined.pop(key)})
                clauses.append({key: value})
            else:
                combined[key] = value
        if clauses:
            combined["$and"] = clauses
        return type(self)(self._doc_cls, combined, self._sort, self._skip, self._limit)

    def sort(self, key_or_list: Any, direction: int = 1) -> DocumentQuery:
        """Order by a field (``sort("views", -1)``) or a list of pairs.

        Accepts the degenerate shapes callers reach for — a bare
        ``(key, direction)`` tuple, a list of plain field names, an empty
        list (no-op) — and normalizes them all to ``[(field, direction)]``.
        """
        if isinstance(key_or_list, (list, tuple)):
            items = list(key_or_list)
            if len(items) == 2 and isinstance(items[0], str) and isinstance(items[1], int):
                pairs = [(items[0], items[1])]
            else:
                pairs = [(item, 1) if isinstance(item, str) else tuple(item) for item in items]
        elif key_or_list is not None:
            pairs = [(key_or_list, direction)]
        else:
            pairs = []
        return type(self)(
            self._doc_cls, self._filter, [*self._sort, *pairs], self._skip, self._limit
        )

    def skip(self, count: int) -> DocumentQuery:
        return type(self)(self._doc_cls, self._filter, self._sort, count, self._limit)

    def limit(self, count: int) -> DocumentQuery:
        return type(self)(self._doc_cls, self._filter, self._sort, self._skip, count)

    def _collection(self) -> AsyncCollection:
        return self._doc_cls._mongo_collection()

    async def get(self) -> list[Document]:
        cursor = self._collection().find(self._filter)
        if self._sort:
            cursor = cursor.sort(self._sort)
        if self._skip:
            cursor = cursor.skip(self._skip)
        if self._limit is not None:
            cursor = cursor.limit(self._limit)
        return [self._doc_cls._from_mongo(doc) async for doc in cursor]

    async def first(self) -> Document | None:
        cursor = self._collection().find(self._filter)
        if self._sort:
            cursor = cursor.sort(self._sort)
        cursor = cursor.skip(self._skip)
        if self._limit is not None:
            cursor = cursor.limit(self._limit)
        doc = await cursor.to_list(length=1)
        return self._doc_cls._from_mongo(doc[0]) if doc else None

    async def count(self) -> int:
        return await self._collection().count_documents(self._filter)

    def _guard_writable(self) -> None:
        # Mongo has no UPDATE ... LIMIT: update_many/delete_many would apply
        # to EVERY match while the caller believes they scoped the write.
        if self._skip or self._limit is not None:
            raise ValueError(
                "update()/delete() cannot apply skip/limit — fetch the matching "
                "ids with .get() and act on {'_id': {'$in': [...]}} instead"
            )

    async def update(self, update: dict[str, Any]) -> int:
        """Apply an update document (``{"$set": {...}}``); returns matched count."""
        self._guard_writable()
        result = await self._collection().update_many(self._filter, update)
        return int(result.matched_count)

    async def delete(self) -> int:
        self._guard_writable()
        result = await self._collection().delete_many(self._filter)
        return int(result.deleted_count)


class Document:
    """Base class for MongoDB collections — Motor-flavored async API."""

    #: Override for irregular names (``Person`` → ``__collection__ = "people"``).
    __collection__: ClassVar[str]
    #: Mass-assignment allowlist (``__fillable__`` semantics).
    __fillable__: ClassVar[tuple[str, ...] | None] = None
    #: Extra denylist — assigning a guarded key raises MassAssignmentError.
    __guarded__: ClassVar[tuple[str, ...]] = ()
    #: Query type ``where()`` builds — subclass bases intercept update()/
    #: delete() through this hook (see fastplace-tenancy's CompanyDocument).
    __query_class__: ClassVar[type[DocumentQuery]] = DocumentQuery

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        snake = _CAMEL.sub("_", cls.__name__).lower()
        cls.__collection__ = cls.__dict__.get("__collection__") or _pluralize(snake)

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)

    @classmethod
    def _mass_assignable(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Filter a mass-assignment payload against the collection's guard rules.

        Mirrors the relational Model: ``__fillable__`` (allowlist —
        non-listed keys are ignored) and ``__guarded__`` (denylist — raises).
        ``_id`` is the document primary key, guarded unless the collection
        lists it in ``__fillable__`` (natural keys opt in explicitly);
        direct instantiation stays the escape hatch.
        """
        from fastplace.errors import MassAssignmentError

        fillable = getattr(cls, "__fillable__", None)
        blocked = {"_id"} | set(getattr(cls, "__guarded__", ()))
        if fillable and "_id" in fillable:
            blocked.discard("_id")
        filtered: dict[str, Any] = {}
        for key, value in values.items():
            if fillable is not None and key not in fillable:
                continue
            if key in blocked:
                raise MassAssignmentError(
                    f"{cls.__collection__}.{key} is guarded from mass assignment"
                )
            filtered[key] = value
        return filtered

    @property
    def id(self) -> Any:
        return self.__dict__.get("_id")

    @classmethod
    def _mongo_collection(cls) -> AsyncCollection:
        return documents_database()[cls.__collection__]

    @classmethod
    def collection(cls) -> AsyncCollection:
        """The raw ``AsyncCollection`` behind this document class.

        The public escape hatch for operations the query builder does not
        model — aggregation pipelines, index management, ``bulkWrite`` —
        without reaching for framework internals. Tenant-scoped bases that
        need the guard to ride along should go through ``where()`` instead;
        nothing here applies filters automatically.
        """
        return cls._mongo_collection()

    @classmethod
    def _from_mongo(cls, doc: dict[str, Any]) -> Document:
        return cls(**doc)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    # -- class-level data access ------------------------------------------------

    @classmethod
    def _build_payload(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Fill annotation defaults — the public face of the payload builder.

        Subclass bases (fastplace-tenancy) stamp their own columns into the
        payload; reaching for the module-private helper would be fragile.
        """
        return _document_payload(cls, data)

    @classmethod
    def where(cls, filter: dict[str, Any] | None = None, /, **equality) -> DocumentQuery:
        return cls.__query_class__(cls).where(filter, **equality)

    @classmethod
    async def find(
        cls,
        filter: dict[str, Any] | None = None,
        *,
        sort: Any = None,
        skip: int = 0,
        limit: int | None = None,
    ) -> list[Document]:
        query = cls.where(filter)
        if isinstance(sort, (list, tuple)) and sort:
            query = query.sort(list(sort))
        elif isinstance(sort, str):
            query = query.sort(sort)
        if skip:
            query = query.skip(skip)
        if limit is not None:
            query = query.limit(limit)
        return await query.get()

    @classmethod
    async def first(cls, filter: dict[str, Any] | None = None, /, **equality) -> Document | None:
        return await cls.where(filter, **equality).first()

    @classmethod
    async def count(cls, filter: dict[str, Any] | None = None, /, **equality) -> int:
        return await cls.where(filter, **equality).count()

    @classmethod
    async def create(cls, **data: Any) -> Document:
        payload = _document_payload(cls, cls._mass_assignable(data))
        result = await cls._mongo_collection().insert_one(payload)
        # Build the instance from the payload — the stored document and the
        # returned one must agree, defaults included.
        return cls(**{**payload, "_id": result.inserted_id})

    @classmethod
    async def insert(cls, documents: list[dict[str, Any]]) -> list[Any]:
        """Insert many payload dicts; returns the generated ``_id`` list."""
        result = await cls._mongo_collection().insert_many(
            [_document_payload(cls, cls._mass_assignable(doc)) for doc in documents]
        )
        return list(result.inserted_ids)

    # -- instance persistence ---------------------------------------------------

    async def update(self, **changes: Any) -> Document:
        """``$set`` the given fields and refresh the local copy."""
        if self.id is None:
            raise LookupError(
                f"{self.__collection__} instance has no _id — create it before updating"
            )
        result = await self._mongo_collection().find_one_and_update(
            {"_id": self.id},
            {"$set": type(self)._mass_assignable(changes)},
            return_document=ReturnDocument.AFTER,
        )
        if result is None:
            raise LookupError(f"{self.__collection__} document {_id_repr(self.id)} vanished")
        self.__dict__.update(result)
        return self

    async def delete(self) -> None:
        if self.id is None:
            raise LookupError(
                f"{self.__collection__} instance has no _id — create it before deleting"
            )
        result = await self._mongo_collection().delete_one({"_id": self.id})
        if result.deleted_count == 0:
            raise LookupError(f"{self.__collection__} document {_id_repr(self.id)} vanished")

    async def reload(self) -> Document:
        fresh = await self._mongo_collection().find_one({"_id": self.id})
        if fresh is None:
            raise LookupError(f"{self.__collection__} document {_id_repr(self.id)} vanished")
        self.__dict__.clear()
        self.__dict__.update(fresh)
        return self


def _id_repr(value: Any) -> Any:
    return str(value)
