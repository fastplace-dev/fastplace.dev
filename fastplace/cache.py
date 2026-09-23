"""Cache — framework cache store with memory, redis, and database drivers.

``cache()`` returns the process-wide store selected by ``CACHE_DRIVER``:
``memory`` (default, zero-dependency dev fallback), ``redis`` (JSON values on
redis.asyncio), or ``database`` (a portable ``cache`` table over SQLAlchemy
Core — the production default alongside redis). All drivers share one async
interface — ``get``, ``put``, ``forget``, ``remember``, ``flush``,
``increment``, ``ttl`` — so controllers and services never care which driver
is configured.

Serialization caveat: the redis and database drivers store JSON, so types JSON
cannot represent (tuples, non-string dict keys, datetimes) do not round-trip
identically — and unserializable values raise :class:`CacheSerializationError`
at ``put()`` time. Code that must be driver-agnostic should cache JSON-shaped
data only.
"""

from __future__ import annotations

import inspect
import json
import time
from collections.abc import Callable
from time import monotonic
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    cast,
    delete,
    insert,
    select,
    update,
)

from fastplace.config import config
from fastplace.errors import CacheSerializationError, ConfigurationError

#: Sentinel distinguishing "key absent" from "key present with value None".
_MISSING = object()


def _validate_ttl(ttl: int | float | None) -> None:
    """redis rejects ``ex <= 0`` server-side; a non-positive ttl is always a bug."""
    if ttl is None:
        return
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
        # A text ttl would TypeError on the <= below — nobody can act on that.
        raise ValueError(f"cache ttl must be a positive number of seconds, got {ttl!r}")
    if ttl <= 0:
        raise ValueError(f"cache ttl must be a positive number of seconds, got {ttl}")


def _default_ttl(ttl: int | float | None) -> int | float | None:
    """Resolve a remember() ttl against the configured CACHE_TTL default."""
    if ttl is not None:
        return ttl
    raw = config("CACHE_TTL", default=3600)
    if isinstance(raw, str):
        # A bare env value stays text when no config-module default is loaded
        # to coerce against — the cache contract is seconds, so coerce here.
        try:
            return int(raw)
        except ValueError:
            pass  # _validate_ttl rejects non-numeric text with a clear error
    return raw


def _counter_horizon(ttl: int | float | None) -> float:
    """Resolve a counter's decay window — counters are always bounded.

    Unlike put() (where ``None`` means "lives forever"), an increment() window
    must exist: an unbounded counter can never release a locked-out key.
    """
    resolved = _default_ttl(ttl)
    if resolved is None:
        raise ValueError("cache ttl must be a positive number of seconds, got None")
    _validate_ttl(resolved)
    return float(resolved)


@runtime_checkable
class CacheStore(Protocol):
    """The async surface every cache driver implements.

    Drivers may serialize values (redis/database store JSON) — tuple/int-key
    fidelity is not guaranteed across drivers.
    """

    async def get(self, key: str) -> Any: ...
    async def put(self, key: str, value: Any, ttl: int | float | None = None) -> None: ...
    async def forget(self, key: str) -> None: ...
    async def flush(self) -> None: ...
    async def remember(
        self, key: str, ttl: int | float | None = None, factory: Callable[[], Any] = lambda: None
    ) -> Any: ...
    async def increment(self, key: str, ttl: int | float | None = None) -> int:
        """Atomic +1 (storing 1 on first hit); expired counters restart at 1."""
        ...

    async def ttl(self, key: str) -> float | None:
        """Seconds remaining before expiry; None when the key is absent (or has no TTL)."""
        ...


# ---------------------------------------------------------------------------
# Memory driver
# ---------------------------------------------------------------------------


class _Entry:
    """One cached value with a monotonic-clock deadline (None = never)."""

    __slots__ = ("deadline", "value")

    def __init__(self, value: Any, deadline: float | None) -> None:
        self.value = value
        self.deadline = deadline


class MemoryCache:
    """In-process dict store with lazy TTL expiry — every op is O(1)."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    async def get(self, key: str) -> Any:
        value = self._get(key)
        return None if value is _MISSING else value

    def _get(self, key: str) -> Any:
        """Internal hit/miss read — ``_MISSING`` means absent or expired."""
        entry = self._entries.get(key)
        if entry is None:
            return _MISSING
        if entry.deadline is not None and monotonic() >= entry.deadline:
            del self._entries[key]  # lazy expiry — no background sweeper needed
            return _MISSING
        return entry.value

    async def put(self, key: str, value: Any, ttl: int | float | None = None) -> None:
        _validate_ttl(ttl)
        deadline = None if ttl is None else monotonic() + ttl
        self._entries[key] = _Entry(value, deadline)

    async def forget(self, key: str) -> None:
        self._entries.pop(key, None)

    async def flush(self) -> None:
        self._entries.clear()

    async def remember(
        self, key: str, ttl: int | float | None = None, factory: Callable[[], Any] = lambda: None
    ) -> Any:
        """Get ``key`` or compute it via ``factory`` (sync or async) and cache it.

        A cached ``None`` is a hit — negative lookups must not re-run the
        factory on every call. ``ttl`` defaults to the configured CACHE_TTL.
        """
        resolved = _default_ttl(ttl)
        _validate_ttl(resolved)
        cached = self._get(key)
        if cached is not _MISSING:
            return cached
        value = factory()
        if inspect.isawaitable(value):
            value = await value
        await self.put(key, value, ttl=resolved)
        return value

    async def increment(self, key: str, ttl: int | float | None = None) -> int:
        """Atomic +1 on the in-process counter; expired counters restart at 1.

        Each hit re-arms the deadline (a sliding window) — the counter is a
        plain stored value, so it follows put() ttl semantics with the
        configured CACHE_TTL as the default horizon.
        """
        current = self._get(key)
        base = current if isinstance(current, int) else 0
        horizon = _counter_horizon(ttl)
        value = int(base) + 1
        self._entries[key] = _Entry(value, monotonic() + horizon)
        return value

    async def ttl(self, key: str) -> float | None:
        entry = self._entries.get(key)
        if entry is None or entry.deadline is None:
            return None
        return max(0.0, entry.deadline - monotonic())


# ---------------------------------------------------------------------------
# Redis driver
# ---------------------------------------------------------------------------


def _redis_available() -> bool:
    try:
        import redis.asyncio  # noqa: F401
    except ImportError:
        return False
    return True


class RedisCache:
    """Redis-backed store — values serialized as JSON, client lazily built.

    Every key carries the ``CACHE_PREFIX`` namespace (default
    ``fastplace:cache:``): the redis DB is typically shared (the SAQ queue
    lives there by default), so ``flush()`` deletes only this namespace —
    never FLUSHDB. The client (or a test fake) can be injected; ``url``
    construction never connects — redis.asyncio only opens connections when
    a command runs.
    """

    def __init__(
        self,
        client: Any | None = None,
        url: str | None = None,
    ) -> None:
        self._client = client
        self._url = url or config("REDIS_URL", default="redis://localhost:6379/0")
        self._prefix = str(config("CACHE_PREFIX", default="fastplace:cache:"))

    @property
    def client(self) -> Any:
        if self._client is None:
            import redis.asyncio

            self._client = redis.asyncio.from_url(self._url)
        return self._client

    def _namespaced(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def get(self, key: str) -> Any:
        raw = await self.client.get(self._namespaced(key))
        if raw is None:
            return None
        return json.loads(raw)

    async def put(self, key: str, value: Any, ttl: int | float | None = None) -> None:
        _validate_ttl(ttl)
        try:
            payload = json.dumps(value)
        except TypeError as exc:
            raise CacheSerializationError(
                f"cache cannot serialize value for key '{key}' "
                f"({type(value).__name__} is not JSON-serializable): {exc}"
            ) from exc
        await self.client.set(self._namespaced(key), payload, ex=ttl)

    async def forget(self, key: str) -> None:
        await self.client.delete(self._namespaced(key))

    async def flush(self) -> None:
        """Delete only this cache's namespace — the DB is shared, FLUSHDB never."""
        keys = [key async for key in self.client.scan_iter(match=f"{self._prefix}*")]
        if keys:
            await self.client.unlink(*keys)

    async def remember(
        self, key: str, ttl: int | float | None = None, factory: Callable[[], Any] = lambda: None
    ) -> Any:
        resolved = _default_ttl(ttl)
        _validate_ttl(resolved)
        # Raw read so a cached JSON null stays a hit.
        raw = await self.client.get(self._namespaced(key))
        if raw is not None:
            return json.loads(raw)
        value = factory()
        if inspect.isawaitable(value):
            value = await value
        await self.put(key, value, ttl=resolved)
        return value

    async def increment(self, key: str, ttl: int | float | None = None) -> int:
        """INCR-compatible counter — only the first hit arms the expiry.

        Counters are stored as plain int text (what INCR itself writes), never
        JSON-quoted, so the value stays INCR-compatible across clients.
        """
        namespaced = self._namespaced(key)
        value = await self.client.incr(namespaced)
        if await self.client.ttl(namespaced) == -1:  # exists, no expiry armed yet
            await self.client.expire(namespaced, int(_counter_horizon(ttl)))
        return int(value)

    async def ttl(self, key: str) -> float | None:
        remaining = await self.client.ttl(self._namespaced(key))
        if remaining == -2:  # key absent
            return None
        return None if remaining == -1 else max(0.0, float(remaining))


# ---------------------------------------------------------------------------
# Database driver
# ---------------------------------------------------------------------------

# Framework-owned metadata (the sessions-table pattern): the cache table is
# infrastructure, not app domain — it lives off Model.metadata so app Alembic
# revisions never depend on it, and the store creates it idempotently.
_db_cache_metadata = MetaData()

_cache_table = Table(
    "cache",
    _db_cache_metadata,
    Column("key", String(255), primary_key=True),
    Column("value", Text, nullable=False),
    # unix seconds; NULL = the row never expires
    Column("expires_at", Integer, nullable=True, index=True),
)


class DatabaseCache:
    """SQLAlchemy Core cache driver — the production default alongside redis.

    Portable across SQLite/PG/MySQL: no dialect-specific upsert syntax (the
    sessions-store UPDATE-then-INSERT pattern), and every op is one statement
    plus a lazy expired-row sweep on read. Counters keep the INCR contract:
    plain int text, restart at 1 once the row's deadline has passed.
    """

    def __init__(self) -> None:
        self._ensured = False

    def _engine(self) -> Any:
        # Function-level import: reset_db() rebinds fastplace.db.db, and every
        # call must see the live binding (tests reload config per case).
        from fastplace.db import db

        return db.manager.engine("default")

    async def _ensure_table(self) -> None:
        """Create the table on first use (checkfirst — idempotent)."""
        if self._ensured:
            return

        def create(sync_conn: Any) -> None:
            _db_cache_metadata.create_all(sync_conn, tables=[_cache_table], checkfirst=True)

        async with self._engine().begin() as conn:
            await conn.run_sync(create)
        self._ensured = True

    def _prefix_head(self) -> str:
        return str(config("CACHE_PREFIX", default="fastplace:cache:"))

    def _key(self, key: str) -> str:
        return f"{self._prefix_head()}{key}"

    async def _lookup(self, stored: str) -> Any:
        """Row read distinguishing absent/expired (``_MISSING``) from a stored null."""
        stmt = select(_cache_table.c.value, _cache_table.c.expires_at).where(
            _cache_table.c.key == stored
        )
        async with self._engine().connect() as conn:
            row = (await conn.execute(stmt)).first()
        if row is None:
            return _MISSING
        if row.expires_at is not None and row.expires_at <= int(time.time()):
            # Lazy sweep: an expired row reads as missing and stops squatting
            # on the key (a later increment must restart at 1, not resurrect).
            async with self._engine().begin() as conn:
                await conn.execute(delete(_cache_table).where(_cache_table.c.key == stored))
            return _MISSING
        return json.loads(row.value)

    async def get(self, key: str) -> Any:
        await self._ensure_table()
        value = await self._lookup(self._key(key))
        return None if value is _MISSING else value

    async def put(self, key: str, value: Any, ttl: int | float | None = None) -> None:
        await self._ensure_table()
        _validate_ttl(ttl)
        try:
            encoded = json.dumps(value)
        except TypeError as exc:
            raise CacheSerializationError(
                f"cache cannot serialize value for key '{key}' "
                f"({type(value).__name__} is not JSON-serializable): {exc}"
            ) from exc
        # Driver parity: put() without a ttl never expires (CACHE_TTL is the
        # remember() default, not a put() default) — NULL means "no deadline".
        expires_at = None if ttl is None else int(time.time()) + int(ttl)
        stored = self._key(key)
        # Portable upsert (SQLite/PG/MySQL) — the sessions-store pattern.
        async with self._engine().begin() as conn:
            result = await conn.execute(
                update(_cache_table)
                .where(_cache_table.c.key == stored)
                .values(value=encoded, expires_at=expires_at)
            )
            if int(result.rowcount or 0) == 0:
                await conn.execute(
                    insert(_cache_table).values(key=stored, value=encoded, expires_at=expires_at)
                )

    async def forget(self, key: str) -> None:
        await self._ensure_table()
        async with self._engine().begin() as conn:
            await conn.execute(delete(_cache_table).where(_cache_table.c.key == self._key(key)))

    async def flush(self) -> None:
        """Delete only this cache's namespace — the table may be shared."""
        await self._ensure_table()
        async with self._engine().begin() as conn:
            await conn.execute(
                delete(_cache_table).where(_cache_table.c.key.like(f"{self._prefix_head()}%"))
            )

    async def remember(
        self, key: str, ttl: int | float | None = None, factory: Callable[[], Any] = lambda: None
    ) -> Any:
        await self._ensure_table()
        resolved = _default_ttl(ttl)
        _validate_ttl(resolved)
        # Sentinel read so a cached JSON null stays a hit (driver parity).
        cached = await self._lookup(self._key(key))
        if cached is not _MISSING:
            return cached
        value = factory()
        if inspect.isawaitable(value):
            value = await value
        await self.put(key, value, ttl=resolved)
        return value

    async def increment(self, key: str, ttl: int | float | None = None) -> int:
        """Expiry-aware +1: first hit stores 1; a hit past expiry restarts at 1."""
        await self._ensure_table()
        horizon = int(_counter_horizon(ttl))
        stored = self._key(key)
        now = int(time.time())
        async with self._engine().begin() as conn:
            result = await conn.execute(
                update(_cache_table)
                .where(_cache_table.c.key == stored)
                .where((_cache_table.c.expires_at.is_(None)) | (_cache_table.c.expires_at > now))
                .values(value=cast(_cache_table.c.value, Integer) + 1)
            )
            if int(result.rowcount or 0) == 0:
                # Absent or stale (expired) row — restart the window at 1.
                await conn.execute(delete(_cache_table).where(_cache_table.c.key == stored))
                await conn.execute(
                    insert(_cache_table).values(key=stored, value="1", expires_at=now + horizon)
                )
                return 1
        # The UPDATE hit a live row — read the incremented value back.
        async with self._engine().connect() as conn:
            row = (
                await conn.execute(select(_cache_table.c.value).where(_cache_table.c.key == stored))
            ).first()
        if row is None:  # pragma: no cover — the row was just updated in-transaction
            return 1
        current = row.value
        if isinstance(current, str):
            current = json.loads(current)
        return int(current)

    async def ttl(self, key: str) -> float | None:
        await self._ensure_table()
        stmt = select(_cache_table.c.expires_at).where(_cache_table.c.key == self._key(key))
        async with self._engine().connect() as conn:
            row = (await conn.execute(stmt)).first()
        if row is None or row.expires_at is None:
            return None
        return max(0.0, float(row.expires_at - int(time.time())))


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------

_default_cache: CacheStore | None = None


def cache() -> CacheStore:
    """The process-wide cache store (``CACHE_DRIVER``, default ``memory``)."""
    global _default_cache
    if _default_cache is None:
        driver = config("CACHE_DRIVER", default="memory")
        if driver == "memory":
            _default_cache = MemoryCache()
        elif driver == "redis":
            if not _redis_available():
                raise ConfigurationError(
                    "CACHE_DRIVER=redis requires the redis library — pip install 'fastplace[queue]'"
                )
            _default_cache = RedisCache()
        elif driver == "database":
            _default_cache = DatabaseCache()
        else:
            raise ConfigurationError(f"unknown CACHE_DRIVER '{driver}'")
    return _default_cache


def reset_cache() -> None:
    """Drop the singleton — tests and config reloads."""
    global _default_cache
    _default_cache = None
