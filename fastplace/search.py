"""Search abstraction — pluggable ``SearchService`` with a database default.

The framework ships no third-party search dependency: the default service
is the relational database itself, using PostgreSQL full-text search
through ``Model.full_text_search()`` behind the ``supports_full_text``
capability flag. Applications register their own service (Meilisearch,
Typesense, an embedding-backed store) and every call site keeps working.

Two surfaces, two jobs. A :class:`SearchService` *answers queries*; a
:class:`SearchEngine` *keeps an index current* (``update``/``delete``/
``flush``). The default engine (:class:`DatabaseSearchEngine`) is
deliberately query-only — the database needs no index maintained — so a
model that opts into index sync (``make_searchable``) needs a real engine
registered, and the default refuses index writes loudly instead of letting
the index drift silently.

Index sync pipeline: ``make_searchable(Model, ...)`` wires the model's
lifecycle events to the ``search_index_sync`` queue job. The record is
serialized at event time (:func:`search_record` — plain dict, never the ORM
instance) and the job applies it to the active engine from the worker side.
Enrollment through ``queue=True`` rides :mod:`fastplace.events` dispatch, so
the enqueue is buffered until the enclosing transaction commits (a rollback
discards it), and a broker outage after commit logs and gives up rather
than failing the durable write — the same contract the domain-event bridge
already gives every model. ``queue=False`` keeps the engine call in-process
(dev/tests: loud failures, no queue needed).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as datetime_time
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

__all__ = [
    "SEARCH_INDEX_SYNC_JOB",
    "DatabaseSearchEngine",
    "DatabaseSearchService",
    "NotSearchable",
    "SearchEngine",
    "SearchNotSupported",
    "SearchService",
    "SearchableModel",
    "ensure_index_sync_job",
    "get_engine",
    "get_search_service",
    "make_searchable",
    "register_engine",
    "register_search_service",
    "reset_engine",
    "reset_search_service",
    "reset_searchable",
    "search_index_sync",
    "search_record",
    "searchable_fields",
    "searchable_models",
    "unregister_searchable",
]

logger = logging.getLogger("fastplace.search")


@runtime_checkable
class SearchService(Protocol):
    """Anything that can answer a free-text query with model-shaped rows."""

    async def search(
        self, query: str, *, model: SearchableModel | None = None, limit: int = 20
    ) -> list[Any]: ...


@runtime_checkable
class SearchableModel(Protocol):
    """A repository model the default service can search (relational ``Model``)."""

    @classmethod
    async def full_text_search(cls, query: str, *, limit: int = 20) -> list[Any]: ...


class SearchNotSupported(RuntimeError):
    """Raised when the active backend cannot serve full-text queries."""


class DatabaseSearchService:
    """Default service — PostgreSQL FTS via ``Model.full_text_search()``.

    ``capabilities`` accepts anything with ``supports_full_text: bool`` and
    ``driver: str`` (the ``db.capabilities`` shape) — pass a stub to fake a
    backend in tests; ``None`` resolves the live ``db.capabilities``.
    """

    def __init__(self, capabilities: Any | None = None) -> None:
        self._capabilities = capabilities

    def _caps(self) -> Any:
        if self._capabilities is not None:
            return self._capabilities
        from fastplace.db import db

        return db.capabilities

    async def search(
        self, query: str, *, model: SearchableModel | None = None, limit: int = 20
    ) -> list[Any]:
        if model is None:
            raise ValueError("model is required — pass the repository model to search")
        caps = self._caps()
        if not caps.supports_full_text:
            raise SearchNotSupported(
                f"backend {caps.driver!r} has no full-text search — use "
                "PostgreSQL or register a custom SearchService "
                "(fastplace.search.register_search_service)"
            )
        return await model.full_text_search(query, limit=limit)


_service: SearchService | None = None


def get_search_service() -> SearchService:
    """The registered service — ``DatabaseSearchService`` until overridden."""
    global _service
    if _service is None:
        _service = DatabaseSearchService()
    return _service


def register_search_service(service: SearchService) -> None:
    """Swap the process-wide search implementation (applications, tests)."""
    if not callable(getattr(service, "search", None)):
        raise TypeError(
            "search service must implement async search(query, *, model=None, limit=20)"
        )
    global _service
    _service = service


def reset_search_service() -> None:
    """Drop any override — back to the database-backed default."""
    global _service
    _service = None


# ---------------------------------------------------------------------------
# engines — the write side of search
# ---------------------------------------------------------------------------

#: Stable dispatch name of the framework-owned index-sync job. Reserved — a
#: collision with an application job fails at registration, on purpose.
SEARCH_INDEX_SYNC_JOB = "search_index_sync"

#: The lifecycle events an enrollment wires, and the index op each maps to.
#: Both delete flavors (soft tombstone, hard row removal) leave the index:
#: the record is serialized from the in-memory instance before the handler
#: runs out of data.
_OP_FOR_EVENT: dict[str, str] = {
    "created": "update",
    "updated": "update",
    "restored": "update",
    "deleted": "delete",
}

_ENGINE_METHODS = ("update", "delete", "flush", "search")


class SearchEngine(ABC):
    """Anything that maintains an index for searchable models.

    ``records`` accepts model instances or already-serialized
    :func:`search_record` dicts — engines normalize through
    :func:`search_record`, which passes dicts through untouched. That one
    rule is what lets the same engine object serve both the in-process path
    (instances straight off a lifecycle event) and the queue path (dicts
    rebuilt from a JSON payload). ``update``/``delete`` return the number of
    records the engine accepted; ``flush`` drops everything indexed for one
    model.
    """

    @abstractmethod
    async def update(self, records: list[Any]) -> int:
        """Upsert records into the index; returns the count accepted."""

    @abstractmethod
    async def delete(self, records: list[Any]) -> int:
        """Remove records from the index; returns the count removed."""

    @abstractmethod
    async def flush(self, model: type[Any]) -> None:
        """Drop everything indexed for one model."""

    @abstractmethod
    async def search(
        self, query: str, *, model: type[Any] | None = None, limit: int = 20
    ) -> list[Any]:
        """Query the index — same contract as ``SearchService.search``."""


_QUERY_ONLY = (
    "the default search engine is query-only — it reads PostgreSQL FTS "
    "directly and holds no index of its own. Register a real engine "
    "(fastplace.search.register_engine) before making models searchable."
)


class DatabaseSearchEngine(SearchEngine):
    """Default engine — query-only, backed by ``Model.full_text_search()``.

    Index writes raise :class:`SearchNotSupported` rather than no-op: the
    database needs no maintained index, but a searchable model pointed at
    this engine is a configuration gap, and silence would let every write
    drift away without a word.

    ``capabilities`` accepts anything with ``supports_full_text: bool`` and
    ``driver: str`` (the ``db.capabilities`` shape) — pass a stub to fake a
    backend in tests; ``None`` resolves the live ``db.capabilities``.
    """

    def __init__(self, capabilities: Any | None = None) -> None:
        self._capabilities = capabilities

    def _caps(self) -> Any:
        if self._capabilities is not None:
            return self._capabilities
        from fastplace.db import db

        return db.capabilities

    async def search(
        self, query: str, *, model: type[Any] | None = None, limit: int = 20
    ) -> list[Any]:
        if model is None:
            raise ValueError("model is required — pass the repository model to search")
        caps = self._caps()
        if not caps.supports_full_text:
            raise SearchNotSupported(
                f"backend {caps.driver!r} has no full-text search — use "
                "PostgreSQL or register a custom engine "
                "(fastplace.search.register_engine)"
            )
        return await model.full_text_search(query, limit=limit)

    async def update(self, records: list[Any]) -> int:
        raise SearchNotSupported(_QUERY_ONLY)

    async def delete(self, records: list[Any]) -> int:
        raise SearchNotSupported(_QUERY_ONLY)

    async def flush(self, model: type[Any]) -> None:
        raise SearchNotSupported(_QUERY_ONLY)


_engine: SearchEngine | None = None


def register_engine(engine: SearchEngine) -> None:
    """Install the process-wide index engine (applications, tests).

    Accepts any object carrying the full async engine surface — subclassing
    :class:`SearchEngine` is welcome but not required, so a test double is
    a plain object with four async methods.
    """
    missing = [name for name in _ENGINE_METHODS if not callable(getattr(engine, name, None))]
    if missing:
        raise TypeError(
            "search engine is missing required async method(s): "
            + ", ".join(missing)
            + f" — implement {_ENGINE_METHODS}"
        )
    global _engine
    _engine = engine  # type: ignore[assignment]


def get_engine() -> SearchEngine:
    """The registered engine — ``DatabaseSearchEngine`` until overridden."""
    global _engine
    if _engine is None:
        _engine = DatabaseSearchEngine()
    return _engine


def reset_engine() -> None:
    """Drop any override — back to the query-only default."""
    global _engine
    _engine = None


# ---------------------------------------------------------------------------
# searchable models — declaration + serialization
# ---------------------------------------------------------------------------


class NotSearchable(RuntimeError):
    """Raised for models without a usable ``__searchable__`` declaration."""


def searchable_fields(model: type[Any]) -> tuple[str, ...]:
    """The model's declared ``__searchable__`` columns, validated.

    Every declared name must be a real column of the mapped table — a typo
    is a :class:`NotSearchable` at enrollment time, not a silent
    half-populated index.
    """
    declared = getattr(model, "__searchable__", None)
    if not declared or isinstance(declared, str) or not isinstance(declared, (list, tuple)):
        raise NotSearchable(
            f"{model.__name__} is not searchable — declare "
            '__searchable__ = ["field", ...] (a list of column names) on the model'
        )
    table = getattr(model, "__table__", None)
    columns = set(table.columns.keys()) if table is not None else set()
    unknown = [name for name in declared if not isinstance(name, str) or name not in columns]
    if unknown:
        raise NotSearchable(
            f"{model.__name__}.__searchable__ names non-column fields: {unknown} — "
            "only real columns can be indexed"
        )
    return tuple(declared)


def _jsonable(value: Any) -> Any:
    """Coerce a column value to a JSON-safe scalar for the queue payload."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def search_record(instance: Any) -> dict[str, Any]:
    """Serialize one model instance (or pass a dict through) for the index.

    The shape is ``{"id": <pk>, **declared searchable fields}`` — always a
    plain dict of JSON-safe scalars, never the ORM object. This is the
    boundary record: it is what lifecycle handlers hand the queue and what
    engines should normalize their ``records`` through.
    """
    if isinstance(instance, dict):
        return dict(instance)
    model = type(instance)
    fields = searchable_fields(model)
    from sqlalchemy import inspect as sa_inspect

    pk_name = sa_inspect(model).primary_key[0].name
    record: dict[str, Any] = {"id": _jsonable(getattr(instance, pk_name))}
    for name in fields:
        record[name] = _jsonable(getattr(instance, name))
    return record


# ---------------------------------------------------------------------------
# enrollment + index-sync pipeline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Enrollment:
    """One model's sync wiring — handlers are removable, engines resolvable."""

    key: str
    model: type[Any]
    engine: SearchEngine | None  # None = resolve through the registry at run time
    queue: bool
    handlers: tuple[tuple[str, Any], ...]


_enrollments: dict[str, _Enrollment] = {}


def searchable_models() -> list[str]:
    """Sorted keys of every enrolled model (introspection, tests)."""
    return sorted(_enrollments)


def reset_searchable() -> None:
    """Clear every enrollment — test isolation."""
    _enrollments.clear()


def make_searchable(
    model: type[Any], *, engine: SearchEngine | None = None, queue: bool = True
) -> None:
    """Enroll a model: lifecycle events now keep its index current.

    ``created``/``updated``/``restored`` upsert the record; ``deleted``
    removes it (soft tombstone and hard ``force_delete`` alike — the record
    is serialized from the in-memory instance either way). Subclasses of an
    enrolled model are NOT indexed under it: enrollment applies to exactly
    that class, so a hierarchy enrolls each indexed class explicitly.

    ``queue=True`` (production default) routes the update through the
    ``search_index_sync`` queue job via :mod:`fastplace.events` — the
    enqueue waits for the enclosing transaction to commit, a rollback
    discards it, and a broker outage after commit logs and gives up instead
    of failing the durable write. ``queue=False`` calls the engine inline
    (dev/tests: no queue, failures are loud like any lifecycle handler).

    ``engine`` pins one engine for this model; ``None`` resolves the
    process-wide registry at run time, so an engine registered after boot
    order is picked up without re-enrolling.
    """
    searchable_fields(model)  # loud, early — before any handler is wired
    key = _enrollment_key(model)
    if key in _enrollments:
        unregister_searchable(model)  # re-enrollment replaces, never doubles
    handlers = tuple(
        (event, _make_handler(key, model, event, engine, queue)) for event in _OP_FOR_EVENT
    )
    for event, handler in handlers:
        model.on(event, handler)
    _enrollments[key] = _Enrollment(
        key=key, model=model, engine=engine, queue=queue, handlers=handlers
    )
    if queue:
        ensure_index_sync_job()


def unregister_searchable(model: type[Any]) -> None:
    """Detach a model's sync handlers (tests, dynamic teardown). Idempotent.

    EventRegistry exposes no removal API, so the handlers come off the
    registry's handler lists by identity — if that structure ever changes,
    this fails loudly (AttributeError), not silently.
    """
    enrollment = _enrollments.pop(_enrollment_key(model), None)
    if enrollment is None:
        return
    registry = getattr(model, "_fastplace_events", None)
    if registry is None:
        return
    for event, handler in enrollment.handlers:
        remaining = [h for h in registry._handlers.get(event, []) if h is not handler]
        if remaining:
            registry._handlers[event] = remaining
        else:
            registry._handlers.pop(event, None)


def _enrollment_key(model: type[Any]) -> str:
    return f"{model.__module__}.{model.__qualname__}"


def _make_handler(
    key: str, model: type[Any], event: str, engine: SearchEngine | None, queue: bool
) -> Any:
    op = _OP_FOR_EVENT[event]

    async def handler(instance: Any) -> None:
        if type(instance) is not model:
            return  # enrollment is exact-class — a subclass indexes itself
        record = search_record(instance)
        await _apply_record(key, engine, queue, op, record)

    return handler


async def _apply_record(
    key: str, engine: SearchEngine | None, queue: bool, op: str, record: dict[str, Any]
) -> None:
    """Route one serialized record to the engine — queued or in-process."""
    if queue:
        ensure_index_sync_job()
        from fastplace.events import DomainEvent, dispatch

        # The auto queue path (to_queue=None) is the whole contract: buffered
        # until commit, discarded on rollback, and an enqueue failure after
        # commit logs and gives up — a durable write never becomes an error.
        await dispatch(
            DomainEvent(SEARCH_INDEX_SYNC_JOB, {"model": key, "op": op, "record": record})
        )
        return
    resolved = engine or get_engine()
    if op == "update":
        await resolved.update([record])
    else:
        await resolved.delete([record])


def ensure_index_sync_job() -> None:
    """(Re-)register the framework index-sync job if the registry lost it.

    Importing this module registers the job; a ``reset_registry()`` elsewhere
    (test isolation) wipes it — enrollment re-arms it so a wiped registry
    degrades to nothing instead of silently dropping every sync.
    """
    from fastplace.queue import Job, jobs

    if SEARCH_INDEX_SYNC_JOB not in jobs():
        Job(SEARCH_INDEX_SYNC_JOB)(search_index_sync)


async def search_index_sync(model: str, op: str, record: dict[str, Any]) -> None:
    """The ``search_index_sync`` queue handler — apply one record to the engine.

    Runs on the worker side, where the engine is resolved fresh (a pinned
    enrollment engine wins, else the process registry). A missing enrollment
    logs and returns — the model was unenrolled between enqueue and run, and
    re-indexing a dead enrollment helps nobody; an unknown op raises so the
    failure lands in the queue's retry/failed machinery where it belongs.
    """
    enrollment = _enrollments.get(model)
    if enrollment is None:
        logger.warning("search index sync for unenrolled model %r — skipping", model)
        return
    engine = enrollment.engine or get_engine()
    if op == "update":
        await engine.update([record])
    elif op == "delete":
        await engine.delete([record])
    else:
        raise ValueError(f"unknown search index op {op!r} — use 'update' or 'delete'")


# The framework's own handler takes its dispatch name at import — any process
# that imports fastplace.search (enrollment does, so does the docs example)
# can dispatch it. Worker processes must import it too: a real engine's
# records only flow if the job is in the worker's registry (one line in
# app/jobs/__init__.py: import fastplace.search with an unused-import marker).
from fastplace.queue import Job  # noqa: E402

Job(SEARCH_INDEX_SYNC_JOB)(search_index_sync)
