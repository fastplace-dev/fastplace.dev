"""Rate limiting — cache-backed counters + the ``throttle:N,D`` middleware (spec §4.19).

Beyond the numeric form, routes may name a limiter — ``throttle:api`` —
resolved from the registry populated with :func:`limit`. A named limiter
returns a :class:`Limit` (or a list of them — all are enforced) whose
optional key resolver scopes the counter per user instead of per IP,
turning "5 per minute per client" into "5 per minute per account".
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from fastplace.cache import CacheStore, cache
from fastplace.errors import ConfigurationError, ThrottleRequestsError
from fastplace.http.middleware import Middleware
from fastplace.http.request import Request
from fastplace.http.response import Response

# A resolver returns the key component for this request, or None to opt the
# request out of the limit entirely (e.g. unauthenticated guests).
LimitResolver = Callable[[Request], "str | None | Awaitable[str | None]"]


@dataclass(frozen=True)
class Limit:
    """One window: at most ``max_attempts`` per ``decay`` seconds."""

    max_attempts: int
    decay: int
    key_resolver: LimitResolver | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool):
            raise ConfigurationError(
                f"Limit.max_attempts must be an int, got {self.max_attempts!r}"
            )
        if not isinstance(self.decay, int) or isinstance(self.decay, bool):
            raise ConfigurationError(f"Limit.decay must be an int, got {self.decay!r}")
        if self.max_attempts < 1 or self.decay < 1:
            raise ConfigurationError(
                "Limit values must be positive — got "
                f"max_attempts={self.max_attempts}, decay={self.decay}"
            )

    @classmethod
    def per_second(cls, max_attempts: int) -> Limit:
        return cls(max_attempts=max_attempts, decay=1)

    @classmethod
    def per_minute(cls, max_attempts: int) -> Limit:
        return cls(max_attempts=max_attempts, decay=60)

    @classmethod
    def per_hour(cls, max_attempts: int) -> Limit:
        return cls(max_attempts=max_attempts, decay=3600)

    @classmethod
    def per_day(cls, max_attempts: int) -> Limit:
        return cls(max_attempts=max_attempts, decay=86400)

    def by(self, resolver: LimitResolver) -> Limit:
        """Scope this limit's counter to a per-request key component.

        The resolver receives the request and returns the component (e.g.
        ``lambda r: r.auth_id`` — any value is stringified). Returning None
        skips the limit for that request, so guests can pass while accounts
        are throttled. Sync or async resolvers both work.
        """
        return replace(self, key_resolver=resolver)


# -- named limiter registry --------------------------------------------------

# Keyed by the route-middleware name; values are sync-or-async callbacks
# answering with a Limit, a list of Limits (all enforced), or None (skip).
_limiters: dict[str, Callable[[Request], Any]] = {}


def limit(name: str, callback: Callable[[Request], Any]) -> None:
    """Register (or replace) the named limiter ``name``.

    Called at bootstrap — top of ``config/app.py`` or a provider — before
    the first request reaches a ``throttle:<name>`` route. Re-registering a
    name replaces the callback; resolution happens per request, so a
    replacement takes effect without a restart.
    """
    if not name:
        raise ConfigurationError("a rate limiter needs a non-empty name")
    _limiters[name] = callback


def reset_limiters() -> None:
    """Drop every registered limiter — test isolation seam."""
    _limiters.clear()


def registered_limits() -> list[str]:
    """Sorted limiter names — tooling's view of the registry."""
    return sorted(_limiters)


# -- middleware ---------------------------------------------------------------


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
    """``throttle:N,D`` or ``throttle:<name>`` — route-level rate limiting.

    Numeric args keep the original contract byte-for-byte: one shared
    counter per ``sha1(ip|path)``, at most N requests per D seconds. A
    single non-numeric arg names a limiter registered with :func:`limit`.

    The registry is consulted per request, not at construction: middleware
    instances are built when routes are declared, while limiters are
    registered in app bootstrap — either order must work. An unknown name
    therefore surfaces as a ConfigurationError on the first request that
    reaches the route, and re-registering a name hot-swaps the behavior.
    """

    def __init__(self, *args: str, store: CacheStore | None = None) -> None:
        self._limiter = RateLimiter(store)
        self.name: str | None = None
        self.max_attempts: int | None = None
        self.decay: int | None = None
        if args and not _is_integer(args[0]):
            if len(args) > 1:
                raise ConfigurationError(
                    f"named throttle middleware takes no extra args (throttle:{args[0]}), got {args!r}"
                )
            self.name = args[0]
            return
        try:
            self.max_attempts = int(args[0]) if len(args) > 0 else 60
            self.decay = int(args[1]) if len(args) > 1 else 60
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"throttle middleware expects integer args (throttle:5,60), got {args!r}"
            ) from exc

    async def handle(self, request: Request, call_next: Any) -> Response:
        if self.name is not None:
            return await self._handle_named(request, call_next)
        assert self.max_attempts is not None and self.decay is not None
        key = hashlib.sha1(f"{request.ip or ''}|{request.path}".encode()).hexdigest()
        if await self._limiter.too_many_attempts(key, self.max_attempts):
            raise await self._throttled(self.max_attempts, key)
        await self._limiter.hit(key, self.decay)
        return await call_next(request)

    async def _handle_named(self, request: Request, call_next: Any) -> Response:
        assert self.name is not None
        callback = _limiters.get(self.name)
        if callback is None:
            raise ConfigurationError(
                f"unknown rate limiter {self.name!r} — register it with "
                f"limit({self.name!r}, callback) in config/app.py before the "
                "first request reaches a throttle route"
            )
        result = callback(request)
        if inspect.isawaitable(result):
            result = await result
        limits = _normalize_limits(self.name, result)

        # Check every limit before hitting any: a request rejected by the
        # third window must not consume budget from the first two. The first
        # exceeded limit (declaration order) supplies the 429. The window
        # fingerprint in the key keeps composed limits from sharing one
        # counter — name|component|path alone would collide the moment a
        # limiter returns two windows.
        checked: list[tuple[Limit, str]] = []
        for lim in limits:
            component = request.ip or ""
            if lim.key_resolver is not None:
                resolved = lim.key_resolver(request)
                if inspect.isawaitable(resolved):
                    resolved = await resolved
                if resolved is None:
                    continue  # resolver opted this request out — not limited
                component = str(resolved)
            key = hashlib.sha1(
                f"{self.name}|{component}|{request.path}|{lim.max_attempts}:{lim.decay}".encode()
            ).hexdigest()
            if await self._limiter.too_many_attempts(key, lim.max_attempts):
                raise await self._throttled(lim.max_attempts, key)
            checked.append((lim, key))
        for lim, key in checked:
            await self._limiter.hit(key, lim.decay)
        return await call_next(request)

    async def _throttled(self, max_attempts: int, key: str) -> ThrottleRequestsError:
        exc = ThrottleRequestsError(retry_after=max(1, await self._limiter.available_in(key)))
        # Additive transport metadata — the kernel error handler can emit
        # these as response headers; the 429 body contract is untouched.
        # Typed as Any because the attribute is transport-only, set per raise.
        throttled: Any = exc
        throttled.headers = {
            "X-RateLimit-Limit": str(max_attempts),
            "X-RateLimit-Remaining": "0",
        }
        return exc


def _is_integer(value: str) -> bool:
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True


def _normalize_limits(name: str, result: Any) -> list[Limit]:
    if result is None:
        return []
    if isinstance(result, Limit):
        return [result]
    if isinstance(result, (list, tuple)):
        for item in result:
            if not isinstance(item, Limit):
                raise ConfigurationError(
                    f"rate limiter {name!r} returned a sequence containing "
                    f"{type(item).__name__} — every entry must be a Limit"
                )
        return list(result)
    raise ConfigurationError(
        f"rate limiter {name!r} must return a Limit, a list of Limits, or "
        f"None — got {type(result).__name__}"
    )
