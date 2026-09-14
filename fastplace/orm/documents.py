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

from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.mongo_client import AsyncMongoClient

#: Brought in lazily via ``fastplace.config`` so document users who never
#: touch MongoDB pay no import-time config read.
_client: AsyncMongoClient | None = None
_database = None  # AsyncDatabase, kept untyped to avoid a second import

_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")
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


def get_documents_client() -> AsyncMongoClient:
    """Process-wide async client, built from ``MONGODB_URL`` on first use."""
    global _client
    if _client is None:
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


def reset_documents() -> None:
    """Drop the client singleton (tests, reconfiguration).

    ``aclose()`` is a coroutine, but teardown is sync — complete it when no
    loop is running, otherwise hand it to the running loop and move on.
    Never raises.
    """
    global _client, _database
    if _client is not None:
        try:
            closing = _client.aclose()
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(closing)
            else:
                loop.create_task(closing)
        except Exception:  # noqa: BLE001 — teardown must never raise
            pass
    _client = None
    _database = None


_MISSING = object()


def _document_payload(doc_cls: type[Document], data: dict[str, Any]) -> dict[str, Any]:
    """Fill annotation defaults for keys the caller did not provide.

    An annotation declares a field; the class attribute is its default
    (a zero-arg callable is a factory, so ``tags: list = list`` yields a
    fresh ``[]`` per insert instead of one shared mutable list).
    """
    payload = dict(data)
    for klass in reversed(doc_cls.__mro__):
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
        """Merge a raw Mongo filter and/or equality kwargs into the match."""
        merged = {**(filter or {}), **equality}
        return DocumentQuery(
            self._doc_cls,
            {**self._filter, **merged},
            self._sort,
            self._skip,
            self._limit,
        )

    def sort(self, key_or_list: Any, direction: int = 1) -> DocumentQuery:
        """Order by a field (``sort("views", -1)``) or a list of pairs."""
        pairs = (
            list(key_or_list)
            if isinstance(key_or_list, (list, tuple))
            and key_or_list
            and isinstance(key_or_list[0], (list, tuple))
            else [(key_or_list, direction)]
        )
        return DocumentQuery(
            self._doc_cls,
            self._filter,
            [*self._sort, *pairs],
            self._skip,
            self._limit,
        )

    def skip(self, count: int) -> DocumentQuery:
        return DocumentQuery(self._doc_cls, self._filter, self._sort, count, self._limit)

    def limit(self, count: int) -> DocumentQuery:
        return DocumentQuery(self._doc_cls, self._filter, self._sort, self._skip, count)

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

    async def update(self, update: dict[str, Any]) -> int:
        """Apply an update document (``{"$set": {...}}``); returns matched count."""
        result = await self._collection().update_many(self._filter, update)
        return int(result.matched_count)

    async def delete(self) -> int:
        result = await self._collection().delete_many(self._filter)
        return int(result.deleted_count)


class Document:
    """Base class for MongoDB collections — Motor-flavored async API."""

    #: Override for irregular names (``Person`` → ``__collection__ = "people"``).
    __collection__: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        snake = _CAMEL.sub("_", cls.__name__).lower()
        cls.__collection__ = cls.__dict__.get("__collection__") or _pluralize(snake)

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)

    @property
    def id(self) -> Any:
        return self.__dict__.get("_id")

    @classmethod
    def _mongo_collection(cls) -> AsyncCollection:
        return documents_database()[cls.__collection__]

    @classmethod
    def _from_mongo(cls, doc: dict[str, Any]) -> Document:
        return cls(**doc)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    # -- class-level data access ------------------------------------------------

    @classmethod
    def where(cls, filter: dict[str, Any] | None = None, /, **equality) -> DocumentQuery:
        return DocumentQuery(cls).where(filter, **equality)

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
        result = await cls._mongo_collection().insert_one(_document_payload(cls, data))
        return cls(**{**data, "_id": result.inserted_id})

    @classmethod
    async def insert(cls, documents: list[dict[str, Any]]) -> list[Any]:
        """Insert many payload dicts; returns the generated ``_id`` list."""
        result = await cls._mongo_collection().insert_many(
            [_document_payload(cls, doc) for doc in documents]
        )
        return list(result.inserted_ids)

    # -- instance persistence ---------------------------------------------------

    async def update(self, **changes: Any) -> Document:
        """``$set`` the given fields and refresh the local copy."""
        result = await self._mongo_collection().find_one_and_update(
            {"_id": self.id},
            {"$set": changes},
            return_document=ReturnDocument.AFTER,
        )
        if result is not None:
            self.__dict__.update(result)
        return self

    async def delete(self) -> None:
        await self._mongo_collection().delete_one({"_id": self.id})

    async def reload(self) -> Document:
        fresh = await self._mongo_collection().find_one({"_id": self.id})
        if fresh is None:
            raise LookupError(f"{self.__collection__} document {_id_repr(self.id)} vanished")
        self.__dict__.clear()
        self.__dict__.update(fresh)
        return self


def _id_repr(value: Any) -> Any:
    return str(value)
