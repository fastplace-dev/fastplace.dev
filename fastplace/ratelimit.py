"""Rate limiting — cache-backed counters + the ``throttle:N,D`` middleware (spec §4.19)."""

from __future__ import annotations

import hashlib
from typing import Any

from fastplace.cache import CacheStore, cache
from fastplace.errors import ConfigurationError, ThrottleRequestsError
from fastplace.http.middleware import Middleware
from fastplace.http.request import Request
from fastplace.http.response import Response


class RateLimiter:
    """Sliding-decay attempt counter over any cache store."""

    def __init__(self, store: CacheStore | None = None) -> None:
        self._store = store if store is not None else cache()
        self._prefix = "ratelimit:"

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def hit(self, key: str, decay: int = 60) -> int:
        """Record one attempt; returns the new count."""
        return await self._store.increment(self._key(key), ttl=decay)

    async def attempts(self, key: str) -> int:
        value = await self._store.get(self._key(key))
        return value if isinstance(value, int) else 0

    async def too_many_attempts(self, key: str, max_attempts: int) -> bool:
        return await self.attempts(key) >= max_attempts

    async def clear(self, key: str) -> None:
        await self._store.forget(self._key(key))

    async def available_in(self, key: str) -> int:
        """Seconds until the counter's TTL frees another attempt."""
        remaining = await self._store.ttl(self._key(key))
        return max(0, int(remaining)) if remaining is not None else 0


class ThrottleMiddleware(Middleware):
    """``throttle:N,D`` — at most N requests per client per D-second window."""

    def __init__(self, *args: str) -> None:
        try:
            self.max_attempts = int(args[0]) if len(args) > 0 else 60
            self.decay = int(args[1]) if len(args) > 1 else 60
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"throttle middleware expects integer args (throttle:5,60), got {args!r}"
            ) from exc
        self._limiter = RateLimiter()

    async def handle(self, request: Request, call_next: Any) -> Response:
        key = hashlib.sha1(f"{request.ip or ''}|{request.path}".encode()).hexdigest()
        if await self._limiter.too_many_attempts(key, self.max_attempts):
            raise ThrottleRequestsError(retry_after=max(1, await self._limiter.available_in(key)))
        await self._limiter.hit(key, self.decay)
        return await call_next(request)
