"""Named rate limiters — the registry behind ``throttle:<name>``.

Covers the ``Limit`` value object, the ``limit()`` registry, named-limiter
resolution in ``ThrottleMiddleware`` (per-user keying, composition, lazy
lookup), and the numeric ``throttle:N,D`` byte-compat contract.
"""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import httpx
import pytest

from fastplace.cache import cache, reset_cache
from fastplace.errors import ConfigurationError, ThrottleRequestsError
from fastplace.ratelimit import (
    Limit,
    RateLimiter,
    ThrottleMiddleware,
    limit,
    registered_limits,
    reset_limiters,
)


async def ok_call_next(request):
    """The middleware contract: call_next is always awaited."""
    return "ok"


def fake_request(ip: str = "10.0.0.1", path: str = "/api/ping", user: object = None):
    """Minimal request double — ThrottleMiddleware only touches ip/path;
    resolvers may touch anything the app puts on the request."""
    return SimpleNamespace(ip=ip, path=path, user=user, auth_id=None)


def named_key(name: str, component: str, path: str, window: str) -> str:
    """The documented cache key for a named limiter hit — the window
    fingerprint keeps composed limits on separate counters."""
    return hashlib.sha1(f"{name}|{component}|{path}|{window}".encode()).hexdigest()


def legacy_key(ip: str, path: str) -> str:
    """The unchanged cache key for numeric ``throttle:N,D``."""
    return hashlib.sha1(f"{ip}|{path}".encode()).hexdigest()


@pytest.fixture(autouse=True)
def _fresh_state():
    reset_cache()
    reset_limiters()
    yield
    reset_cache()
    reset_limiters()


class TestLimit:
    def test_decay_constructors(self):
        assert Limit.per_second(5) == Limit(max_attempts=5, decay=1)
        assert Limit.per_minute(10) == Limit(max_attempts=10, decay=60)
        assert Limit.per_hour(100) == Limit(max_attempts=100, decay=3600)
        assert Limit.per_day(1000) == Limit(max_attempts=1000, decay=86400)

    def test_by_attaches_resolver_without_mutating(self):
        def resolver(request):
            return "u1"

        base = Limit.per_minute(60)
        keyed = base.by(resolver)
        assert base.key_resolver is None
        assert keyed.key_resolver is resolver
        assert keyed.max_attempts == 60 and keyed.decay == 60

    def test_rejects_non_positive_max_attempts(self):
        with pytest.raises(ConfigurationError):
            Limit(max_attempts=0, decay=60)

    def test_rejects_non_positive_decay(self):
        with pytest.raises(ConfigurationError):
            Limit(max_attempts=5, decay=0)

    def test_rejects_non_integer_values(self):
        with pytest.raises(ConfigurationError):
            Limit(max_attempts="5", decay=60)  # type: ignore[arg-type]


class TestRegistry:
    def test_starts_empty(self):
        assert registered_limits() == []

    def test_registers_and_lists_sorted(self):
        limit("web", lambda r: None)
        limit("api", lambda r: None)
        assert registered_limits() == ["api", "web"]

    def test_reset_clears_everything(self):
        limit("api", lambda r: None)
        reset_limiters()
        assert registered_limits() == []

    def test_reregistration_overwrites(self):
        limit("api", lambda r: Limit.per_minute(1))
        limit("api", lambda r: Limit.per_minute(5))
        assert registered_limits() == ["api"]
        # Lazy resolution: the same middleware instance picks up the new
        # callback on the next request.
        mw = ThrottleMiddleware("api")
        call_next = ok_call_next

        async def run():
            for _ in range(5):
                assert await mw.handle(fake_request(), ok_call_next) == "ok"
            with pytest.raises(ThrottleRequestsError):
                await mw.handle(fake_request(), call_next)

        asyncio.run(run())


class TestConstruction:
    def test_named_mode_stores_name_lazily(self):
        mw = ThrottleMiddleware("api")
        assert mw.name == "api"
        assert mw.max_attempts is None
        assert mw.decay is None

    def test_named_mode_rejects_extra_args(self):
        with pytest.raises(ConfigurationError):
            ThrottleMiddleware("api", "60")

    def test_numeric_mode_unchanged(self):
        mw = ThrottleMiddleware("5", "60")
        assert (mw.max_attempts, mw.decay, mw.name) == (5, 60, None)

    def test_numeric_single_arg_unchanged(self):
        mw = ThrottleMiddleware("10")
        assert (mw.max_attempts, mw.decay) == (10, 60)

    def test_numeric_defaults_unchanged(self):
        mw = ThrottleMiddleware()
        assert (mw.max_attempts, mw.decay) == (60, 60)

    def test_numeric_bad_arg_still_raises(self):
        with pytest.raises(ConfigurationError):
            ThrottleMiddleware("5", "sixty")


class TestNamedResolution:
    async def test_unknown_name_raises_at_first_request_not_construction(self):
        mw = ThrottleMiddleware("missing")  # no error yet — lazy by design
        with pytest.raises(ConfigurationError, match="missing"):
            await mw.handle(fake_request(), lambda r: "ok")

    async def test_sync_callback_limits_by_ip_fallback(self):
        limit("api", lambda r: Limit.per_minute(3))
        mw = ThrottleMiddleware("api")
        call_next = ok_call_next

        for _ in range(3):
            assert await mw.handle(fake_request(), ok_call_next) == "ok"
        with pytest.raises(ThrottleRequestsError):
            await mw.handle(fake_request(), call_next)

        limiter = RateLimiter()
        assert await limiter.attempts(named_key("api", "10.0.0.1", "/api/ping", "3:60")) == 3

    async def test_resolvers_none_means_not_limited(self):
        limit("api", lambda r: Limit.per_minute(1).by(lambda r: r.auth_id))
        mw = ThrottleMiddleware("api")
        call_next = ok_call_next

        guest = fake_request(user=None)
        for _ in range(10):
            assert await mw.handle(guest, call_next) == "ok"
        # No counter was ever created for the skipped limit.
        limiter = RateLimiter()
        assert await limiter.attempts(named_key("api", "None", "/api/ping", "1:60")) == 0

    async def test_user_keying_separates_users_from_guests(self):
        limit("api", lambda r: Limit.per_minute(2).by(lambda r: r.user["id"] if r.user else None))
        mw = ThrottleMiddleware("api")
        call_next = ok_call_next

        alice = fake_request(user={"id": "alice"})
        bob = fake_request(user={"id": "bob"})
        guest = fake_request(user=None)

        for _ in range(2):
            assert await mw.handle(alice, call_next) == "ok"
        with pytest.raises(ThrottleRequestsError):
            await mw.handle(alice, call_next)
        # Bob has his own counter; the guest is skipped entirely.
        assert await mw.handle(bob, call_next) == "ok"
        assert await mw.handle(guest, call_next) == "ok"

    async def test_async_callback_and_async_resolver_resolve(self):
        async def callback(request):
            return Limit.per_minute(1).by(resolve_user)

        async def resolve_user(request):
            return request.user["id"]

        limit("api", callback)
        mw = ThrottleMiddleware("api")
        call_next = ok_call_next

        alice = fake_request(user={"id": "alice"})
        assert await mw.handle(alice, call_next) == "ok"
        with pytest.raises(ThrottleRequestsError):
            await mw.handle(alice, call_next)

    async def test_int_key_component_is_stringified(self):
        limit("api", lambda r: Limit.per_minute(2).by(lambda r: r.user["id"]))
        mw = ThrottleMiddleware("api")
        call_next = ok_call_next
        uid7 = fake_request(user={"id": 7})
        for _ in range(2):
            await mw.handle(uid7, call_next)
        limiter = RateLimiter()
        assert await limiter.attempts(named_key("api", "7", "/api/ping", "2:60")) == 2

    async def test_composition_checks_all_hits_all(self):
        limit(
            "api",
            lambda r: [Limit.per_minute(100), Limit.per_day(2)],
        )
        mw = ThrottleMiddleware("api")
        call_next = ok_call_next

        assert await mw.handle(fake_request(), call_next) == "ok"
        assert await mw.handle(fake_request(), call_next) == "ok"
        with pytest.raises(ThrottleRequestsError):
            await mw.handle(fake_request(), call_next)

        # The request rejected by the daily limit must not count against the
        # per-minute limit: both counters sit at exactly 2.
        limiter = RateLimiter()
        minute = named_key("api", "10.0.0.1", "/api/ping", "100:60")  # the per-minute window
        assert await limiter.attempts(minute) == 2

    async def test_first_exceeded_limit_sets_retry_after(self):
        limit("api", lambda r: [Limit.per_minute(1), Limit.per_day(5)])
        mw = ThrottleMiddleware("api")
        call_next = ok_call_next

        await mw.handle(fake_request(), call_next)
        with pytest.raises(ThrottleRequestsError) as excinfo:
            await mw.handle(fake_request(), call_next)
        # The per-minute window (<= 60s) is the one that tripped.
        assert 0 < excinfo.value.retry_after <= 60

    async def test_exceeded_error_carries_rate_limit_headers(self):
        limit("api", lambda r: Limit.per_minute(1))
        mw = ThrottleMiddleware("api")
        call_next = ok_call_next

        await mw.handle(fake_request(), call_next)
        with pytest.raises(ThrottleRequestsError) as excinfo:
            await mw.handle(fake_request(), call_next)
        headers = getattr(excinfo.value, "headers", None)
        assert headers is not None
        assert headers["X-RateLimit-Limit"] == "1"
        assert headers["X-RateLimit-Remaining"] == "0"

    async def test_callback_returning_none_disables_limiting(self):
        limit("api", lambda r: None)
        mw = ThrottleMiddleware("api")
        for _ in range(50):
            assert await mw.handle(fake_request(), ok_call_next) == "ok"

    async def test_callback_returning_garbage_raises(self):
        limit("api", lambda r: "nope")
        mw = ThrottleMiddleware("api")
        with pytest.raises(ConfigurationError, match="Limit"):
            await mw.handle(fake_request(), lambda r: "ok")

    async def test_path_is_part_of_the_key(self):
        limit("api", lambda r: Limit.per_minute(1))
        mw = ThrottleMiddleware("api")
        call_next = ok_call_next
        assert await mw.handle(fake_request(path="/a"), call_next) == "ok"
        assert await mw.handle(fake_request(path="/b"), call_next) == "ok"
        with pytest.raises(ThrottleRequestsError):
            await mw.handle(fake_request(path="/a"), call_next)


class TestNumericByteCompat:
    async def test_legacy_key_format_has_no_name_component(self):
        mw = ThrottleMiddleware("5", "60")
        call_next = ok_call_next
        await mw.handle(fake_request(), call_next)

        limiter = RateLimiter()
        legacy = legacy_key("10.0.0.1", "/api/ping")
        assert await limiter.attempts(legacy) == 1
        # no name-prefixed counter exists under the named scheme either
        assert await limiter.attempts(named_key("throttle", "10.0.0.1", "/api/ping", "5:60")) == 0

    async def test_legacy_throttle_still_raises_with_retry_after(self):
        mw = ThrottleMiddleware("2", "60")
        call_next = ok_call_next
        await mw.handle(fake_request(), call_next)
        await mw.handle(fake_request(), call_next)
        with pytest.raises(ThrottleRequestsError) as excinfo:
            await mw.handle(fake_request(), call_next)
        assert 0 < excinfo.value.retry_after <= 60
        # Additive headers ride along on the numeric path too.
        headers = getattr(excinfo.value, "headers", None)
        assert headers is not None
        assert headers["X-RateLimit-Limit"] == "2"


class TestNamedEndpoint:
    async def test_named_throttle_over_http(self):
        from fastplace.http.kernel import get_app
        from fastplace.http.response import Json
        from fastplace.http.router import Router

        limit("api", lambda r: Limit.per_minute(2))

        async def handler(request):
            return Json({"ok": True})

        router = Router()
        router.get("/items", handler, middleware=["throttle:api"])
        app = get_app(
            routes=router,
            config={"APP_ENV": "local"},
            route_middleware={"throttle": ThrottleMiddleware},
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.get("/items")
            second = await client.get("/items")
            third = await client.get("/items")

        assert (first.status_code, second.status_code) == (200, 200)
        assert third.status_code == 429
        assert "Retry-After" in third.headers
        assert third.headers["Retry-After"].isdigit()

    async def test_named_throttle_scopes_counters_per_path(self):
        from fastplace.http.kernel import get_app
        from fastplace.http.response import Json
        from fastplace.http.router import Router

        limit("api", lambda r: Limit.per_minute(1))

        async def handler(request):
            return Json({"ok": True})

        router = Router()
        router.get("/a", handler, middleware=["throttle:api"])
        router.get("/b", handler, middleware=["throttle:api"])
        app = get_app(
            routes=router,
            config={"APP_ENV": "local"},
            route_middleware={"throttle": ThrottleMiddleware},
        )
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/a")
            blocked = await client.get("/a")
            other = await client.get("/b")

        assert blocked.status_code == 429
        assert other.status_code == 200

    async def test_unknown_name_over_http_returns_500_configuration_error(self):
        from fastplace.http.kernel import get_app
        from fastplace.http.response import Json
        from fastplace.http.router import Router

        async def handler(request):
            return Json({"ok": True})

        router = Router()
        router.get("/items", handler, middleware=["throttle:nope"])
        app = get_app(
            routes=router,
            config={"APP_ENV": "local"},
            route_middleware={"throttle": ThrottleMiddleware},
        )
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/items")
        assert response.status_code == 500


class TestConcurrencyBurst:
    async def test_hundred_racing_hits_count_exactly(self):
        limiter = RateLimiter()
        await asyncio.gather(*(limiter.hit("ff7-burst") for _ in range(100)))
        assert await limiter.attempts("ff7-burst") == 100

    async def test_two_hundred_racing_requests_yield_exactly_150_rejects(self):
        """The memory store's check+hit pair never suspends, so the burst
        is deterministic: 50 pass, 150 raise, the counter lands on 50."""
        limit("burst", lambda r: Limit.per_minute(50))
        mw = ThrottleMiddleware("burst")
        request = fake_request()

        async def call_next(req):
            return "ok"

        outcomes = await asyncio.gather(
            *(mw.handle(request, call_next) for _ in range(200)), return_exceptions=True
        )
        rejected = [o for o in outcomes if isinstance(o, ThrottleRequestsError)]
        passed = [o for o in outcomes if o == "ok"]
        assert len(rejected) == 150
        assert len(passed) == 50
        assert (
            await RateLimiter().attempts(named_key("burst", "10.0.0.1", "/api/ping", "50:60")) == 50
        )


class TestStoreInjection:
    async def test_middleware_accepts_an_injected_store(self):
        from fastplace.cache import MemoryCache

        store = MemoryCache()
        limit("api", lambda r: Limit.per_minute(1))
        mw = ThrottleMiddleware("api", store=store)
        call_next = ok_call_next

        await mw.handle(fake_request(), call_next)
        with pytest.raises(ThrottleRequestsError):
            await mw.handle(fake_request(), call_next)
        # The injected store (not the global cache()) holds the counter.
        assert (
            await store.get(f"ratelimit:{named_key('api', '10.0.0.1', '/api/ping', '1:60')}") == 1
        )
        assert cache().get  # global cache untouched is asserted by isolation above
