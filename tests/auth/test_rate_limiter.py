"""T4 — login rate limiting + throttle route middleware (spec §4.19)."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from fastplace.cache import reset_cache
from fastplace.ratelimit import RateLimiter, ThrottleMiddleware


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_cache()
    yield
    reset_cache()


class TestRateLimiter:
    async def test_hit_counts_attempts(self):
        limiter = RateLimiter()
        assert await limiter.hit("k") == 1
        assert await limiter.hit("k") == 2

    async def test_too_many_attempts_flips_at_the_threshold(self):
        limiter = RateLimiter()
        for _ in range(5):
            await limiter.hit("k")
        assert await limiter.too_many_attempts("k", 5) is True
        assert await limiter.too_many_attempts("k", 6) is False

    async def test_clear_resets_the_counter(self):
        limiter = RateLimiter()
        for _ in range(3):
            await limiter.hit("k")
        await limiter.clear("k")
        assert await limiter.attempts("k") == 0

    async def test_lockout_is_isolated_per_email(self):
        # Review Focus #3: the key is the caller's — one email's lockout
        # never locks out another.
        limiter = RateLimiter()
        key_a = hashlib.sha1(b"a@example.test|10.0.0.1").hexdigest()
        key_b = hashlib.sha1(b"b@example.test|10.0.0.1").hexdigest()
        for _ in range(5):
            await limiter.hit(key_a)
        assert await limiter.too_many_attempts(key_a, 5) is True
        assert await limiter.too_many_attempts(key_b, 5) is False

    async def test_available_in_reports_remaining_seconds(self):
        limiter = RateLimiter()
        for _ in range(5):
            await limiter.hit("k", decay=60)
        remaining = await limiter.available_in("k")
        assert 0 < remaining <= 60


class TestThrottleMiddlewareUnit:
    def test_parses_max_attempts_and_decay(self):
        mw = ThrottleMiddleware("5", "60")
        assert mw.max_attempts == 5
        assert mw.decay == 60

    def test_defaults_when_no_args(self):
        mw = ThrottleMiddleware()
        assert mw.max_attempts == 60
        assert mw.decay == 60

    def test_bad_args_raise_configuration_error(self):
        from fastplace.errors import ConfigurationError

        # A single non-numeric arg is a named-limiter reference (resolved
        # lazily) — only a non-numeric PAIR still fails at construction.
        with pytest.raises(ConfigurationError):
            ThrottleMiddleware("5", "sixty")


class TestThrottleEndpoint:
    async def test_sixth_request_within_limit_returns_429_with_retry_after(self):
        from fastplace.http.kernel import get_app
        from fastplace.http.response import Json
        from fastplace.http.router import Router

        async def handler(request):
            return Json({"ok": True})

        router = Router()
        router.post("/ping", handler, middleware=["throttle:5,60"])
        app = get_app(
            routes=router,
            config={"APP_ENV": "local"},
            route_middleware={"throttle": ThrottleMiddleware},
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = [await client.post("/ping") for _ in range(6)]

        assert [r.status_code for r in responses[:5]] == [200] * 5
        assert responses[5].status_code == 429
        assert "Retry-After" in responses[5].headers
        assert responses[5].headers["Retry-After"].isdigit()


async def test_throttle_error_carries_retry_after_header():
    """The kernel error handler emits Retry-After for throttle errors."""
    from fastplace.errors import ThrottleRequestsError
    from fastplace.http.kernel import get_app
    from fastplace.http.router import Router

    async def boom(request):
        raise ThrottleRequestsError(retry_after=77)

    router = Router()
    router.get("/boom", boom)

    app = get_app(routes=router, config={"APP_ENV": "local"})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/boom")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "77"
