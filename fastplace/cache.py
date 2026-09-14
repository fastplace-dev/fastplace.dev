"""Cache — framework cache store with memory and redis drivers.

``cache()`` returns the process-wide store selected by ``CACHE_DRIVER``:
``memory`` (default, zero-dependency dev fallback) or ``redis`` (JSON values
on redis.asyncio). Both drivers share one async interface — ``get``, ``put``,
``forget``, ``remember``, ``flush`` — so controllers and services never care
which driver is configured.

Serialization caveat: the redis driver stores JSON, so types JSON cannot
represent (tuples, non-string dict keys, datetimes) do not round-trip
identically — and unserializable values raise :class:`CacheSerializationError`
at ``put()`` time. Code that must be driver-agnostic should cache JSON-shaped
data only.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from time import monotonic
from typing import Any, Protocol, runtime_checkable

from fastplace.config import config
from fastplace.errors import CacheSerializationError, ConfigurationError

#: Sentinel distinguishing "key absent" from "key present with value None".
_MISSING = object()


def _validate_ttl(ttl: int | float | None) -> None:
    """redis rejects ``ex <= 0`` server-side; a non-positive ttl is always a bug."""
    if ttl is not None and ttl <= 0:
        raise ValueError(f"cache ttl must be a positive number of seconds, got {ttl}")


def _default_ttl(ttl: int | float | None) -> int | float | None:
    """Resolve a remember() ttl against the configured CACHE_TTL default."""
    return config("CACHE_TTL", default=3600) if ttl is None else ttl


@runtime_checkable
class CacheStore(Protocol):
    """The async surface every cache driver implements.

    Drivers may serialize values (redis stores JSON) — tuple/int-key fidelity
    is not guaranteed across drivers.
    """

    async def get(self, key: str) -> Any: ...
    async def put(self, key: str, value: Any, ttl: int | float | None = None) -> None: ...
    async def forget(self, key: str) -> None: ...
    async def flush(self) -> None: ...
    async def remember(
        self, key: str, ttl: int | float | None = None, factory: Callable[[], Any] = lambda: None
    ) -> Any: ...


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
        else:
            raise ConfigurationError(f"unknown CACHE_DRIVER '{driver}'")
    return _default_cache


def reset_cache() -> None:
    """Drop the singleton — tests and config reloads."""
    global _default_cache
    _default_cache = None
