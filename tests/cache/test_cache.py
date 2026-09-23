"""T4.8 — cache groundwork: memory driver, redis driver (stubbed), factory.

The redis driver is exercised exclusively against an injected fake client —
tests must never contact a live redis server.
"""

from __future__ import annotations

import json

import pytest

from fastplace.cache import (
    DatabaseCache,
    MemoryCache,
    RedisCache,
    cache,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_cache()
    yield
    reset_cache()


# ---------------------------------------------------------------------------
# MemoryCache
# ---------------------------------------------------------------------------


async def test_memory_put_get_roundtrip():
    store = MemoryCache()
    await store.put("greeting", {"hello": "world"})
    assert await store.get("greeting") == {"hello": "world"}


async def test_memory_get_missing_returns_none():
    assert await MemoryCache().get("nope") is None


async def test_memory_forget_removes_only_that_key():
    store = MemoryCache()
    await store.put("a", 1)
    await store.put("b", 2)
    await store.forget("a")
    assert await store.get("a") is None
    assert await store.get("b") == 2


async def test_memory_flush_clears_everything():
    store = MemoryCache()
    await store.put("a", 1)
    await store.put("b", 2)
    await store.flush()
    assert await store.get("a") is None
    assert await store.get("b") is None


async def test_memory_ttl_expires_lazily(monkeypatch):
    store = MemoryCache()
    clock = {"now": 0.0}
    monkeypatch.setattr("fastplace.cache.monotonic", lambda: clock["now"])

    await store.put("ephemeral", "value", ttl=10)
    clock["now"] = 5.0
    assert await store.get("ephemeral") == "value"
    clock["now"] = 10.5  # past the deadline
    assert await store.get("ephemeral") is None


async def test_memory_entry_without_ttl_lives_forever(monkeypatch):
    store = MemoryCache()
    clock = {"now": 0.0}
    monkeypatch.setattr("fastplace.cache.monotonic", lambda: clock["now"])

    await store.put("durable", "value")  # no ttl → no expiry
    clock["now"] = 10_000_000.0
    assert await store.get("durable") == "value"


async def test_memory_remember_caches_factory_result():
    store = MemoryCache()
    calls = []

    def factory():
        calls.append(1)
        return {"computed": True}

    first = await store.remember("derived", ttl=60, factory=factory)
    second = await store.remember("derived", ttl=60, factory=factory)
    assert first == {"computed": True}
    assert second == first
    assert len(calls) == 1  # factory ran once; second read hit the cache


async def test_memory_remember_supports_async_factory():
    store = MemoryCache()

    async def factory():
        return "async-value"

    assert await store.remember("akey", ttl=60, factory=factory) == "async-value"
    assert await store.get("akey") == "async-value"


async def test_memory_remember_recomputes_after_expiry(monkeypatch):
    store = MemoryCache()
    clock = {"now": 0.0}
    monkeypatch.setattr("fastplace.cache.monotonic", lambda: clock["now"])
    calls = []

    def factory():
        calls.append(clock["now"])
        return f"at-{len(calls)}"

    assert await store.remember("r", ttl=10, factory=factory) == "at-1"
    clock["now"] = 11.0
    assert await store.remember("r", ttl=10, factory=factory) == "at-2"
    assert calls == [0.0, 11.0]


async def test_memory_expired_entry_is_dropped_on_read(monkeypatch):
    store = MemoryCache()
    clock = {"now": 0.0}
    monkeypatch.setattr("fastplace.cache.monotonic", lambda: clock["now"])

    await store.put("gone", "x", ttl=1)
    clock["now"] = 2.0
    await store.get("gone")
    assert "gone" not in store._entries  # lazy expiry removed it, no leak


# ---------------------------------------------------------------------------
# MemoryCache — increment / ttl primitives (login throttling groundwork)
# ---------------------------------------------------------------------------


class TestMemoryIncrement:
    async def test_increment_counts_up(self):
        store = MemoryCache()
        assert await store.increment("hits") == 1
        assert await store.increment("hits") == 2
        assert await store.increment("hits") == 3

    async def test_ttl_reports_remaining_seconds(self):
        store = MemoryCache()
        await store.increment("hits", ttl=60)
        remaining = await store.ttl("hits")
        assert remaining is not None and 0 < remaining <= 60

    async def test_ttl_is_none_for_missing_keys(self):
        assert await MemoryCache().ttl("nope") is None

    async def test_ttl_is_none_for_an_entry_without_a_deadline(self):
        # put() without a ttl lives forever — there is no expiry to report.
        store = MemoryCache()
        await store.put("durable", 1)
        assert await store.ttl("durable") is None

    async def test_increment_restarts_after_expiry(self):
        # An expired counter reads as missing, not as a stale integer — the
        # next increment restarts at 1 (fresh decay window).
        store = MemoryCache()
        await store.increment("hits", ttl=1)
        # Force the deadline into the past (the _Entry deadline is monotonic).
        store._entries["hits"] = store._entries["hits"].__class__(store._entries["hits"].value, 0.0)
        assert await store.get("hits") is None  # expired row reads as missing
        assert await store.increment("hits") == 1


# ---------------------------------------------------------------------------
# RedisCache — injected fake client only, never a live server
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal async redis stub recording every command."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.commands: list[tuple] = []
        # increment/ttl surface: remaining-seconds-per-key + expire call count.
        self.deadline: dict[str, int] = {}
        self.expire_calls = 0

    async def set(self, key, value, ex=None):
        self.commands.append(("set", key, value, ex))
        self.store[key] = value
        if ex is None:
            self.deadline.pop(key, None)
        else:
            self.deadline[key] = int(ex)

    async def get(self, key):
        self.commands.append(("get", key))
        return self.store.get(key)

    async def delete(self, key):
        self.commands.append(("delete", key))
        self.store.pop(key, None)
        self.deadline.pop(key, None)

    async def incr(self, key):
        # Redis INCR works on plain int text — the driver must store counters
        # that way, never JSON-quoted ("3", not "\"3\"" or "3.0").
        self.commands.append(("incr", key))
        current = int(self.store.get(key, "0")) + 1
        self.store[key] = str(current)
        return current

    async def ttl(self, key):
        # Redis semantics: -2 = key absent, -1 = no expiry, else seconds left.
        self.commands.append(("ttl", key))
        if key not in self.store:
            return -2
        return self.deadline.get(key, -1)

    async def expire(self, key, seconds):
        self.commands.append(("expire", key, seconds))
        self.deadline[key] = int(seconds)
        self.expire_calls += 1

    async def scan_iter(self, match=None):
        # Mirrors redis.asyncio: scan_iter is an async iterator.
        import fnmatch

        for key in list(self.store):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    async def unlink(self, *keys):
        self.commands.append(("unlink", keys))
        for key in keys:
            self.store.pop(key, None)
            self.deadline.pop(key, None)


async def test_redis_put_get_roundtrip_json():
    client = FakeRedis()
    store = RedisCache(client=client)
    await store.put("user:7", {"name": "Firoz"})
    # serialized as JSON on the wire under the cache namespace, deserialized on read
    assert client.store["fastplace:cache:user:7"] == json.dumps({"name": "Firoz"})
    assert await store.get("user:7") == {"name": "Firoz"}


async def test_redis_get_missing_returns_none():
    assert await RedisCache(client=FakeRedis()).get("nope") is None


async def test_redis_put_passes_ttl_as_ex():
    client = FakeRedis()
    await RedisCache(client=client).put("k", "v", ttl=90)
    assert client.commands[-1] == ("set", "fastplace:cache:k", json.dumps("v"), 90)


async def test_redis_put_without_ttl_has_no_ex():
    client = FakeRedis()
    await RedisCache(client=client).put("k", "v")
    assert client.commands[-1] == ("set", "fastplace:cache:k", json.dumps("v"), None)


async def test_redis_forget_deletes():
    client = FakeRedis()
    store = RedisCache(client=client)
    await store.put("k", "v")
    await store.forget("k")
    assert await store.get("k") is None
    assert ("delete", "fastplace:cache:k") in client.commands


async def test_redis_keys_are_namespaced():
    # The cache shares its redis DB with other tenants (SAQ queues by
    # default) — every key must carry the cache prefix.
    client = FakeRedis()
    await RedisCache(client=client).put("user:7", {"name": "Firoz"})
    assert list(client.store) == ["fastplace:cache:user:7"]


async def test_redis_flush_deletes_only_the_cache_namespace():
    # FLUSHDB would destroy the whole DB (queued jobs included) — flush must
    # evict only namespaced cache keys.
    client = FakeRedis()
    store = RedisCache(client=client)
    await store.put("k1", "v")
    await store.put("k2", "v")
    client.store["saq:queue:fastplace"] = "untouched"
    await store.flush()
    assert await store.get("k1") is None
    assert await store.get("k2") is None
    assert client.store["saq:queue:fastplace"] == "untouched"


async def test_redis_prefix_is_configurable(monkeypatch):
    monkeypatch.setenv("CACHE_PREFIX", "myapp:cache:")
    client = FakeRedis()
    await RedisCache(client=client).put("k", "v")
    assert list(client.store) == ["myapp:cache:k"]


async def test_redis_remember_misses_then_populates():
    client = FakeRedis()
    store = RedisCache(client=client)
    calls = []

    async def factory():
        calls.append(1)
        return ["item"]

    assert await store.remember("list", ttl=30, factory=factory) == ["item"]
    assert await store.remember("list", ttl=30, factory=factory) == ["item"]
    assert len(calls) == 1


def test_redis_lazy_client_construction_is_offline_safe():
    # from_url builds a client pool without connecting — construction must
    # never touch the network.
    store = RedisCache(url="redis://localhost:6379/9")
    assert store._client is None  # not even built yet


async def test_redis_increment_sets_expiry_once():
    # Counters stay plain int text (INCR-compatible) and only the first INCR
    # arms the TTL — the decay window is fixed, not slid by every hit.
    client = FakeRedis()
    store = RedisCache(client=client)
    assert await store.increment("hits", ttl=60) == 1
    assert await store.increment("hits", ttl=60) == 2
    assert await store.increment("hits", ttl=60) == 3
    assert client.store["fastplace:cache:hits"] == "3"
    assert client.expire_calls == 1


async def test_redis_ttl_reports_remaining():
    client = FakeRedis()
    client.store["fastplace:cache:hits"] = "3"
    client.deadline["fastplace:cache:hits"] = 41
    store = RedisCache(client=client)
    assert await store.ttl("hits") == 41.0


async def test_redis_ttl_is_none_for_missing_keys():
    assert await RedisCache(client=FakeRedis()).ttl("nope") is None


# ---------------------------------------------------------------------------
# review hardening: None hits, ttl validation, CACHE_TTL, serialization errors
# ---------------------------------------------------------------------------


async def test_memory_remember_caches_a_none_result():
    # None is a legitimate cached value (negative lookups) — the factory
    # must not re-run on every call.
    store = MemoryCache()
    calls = []

    def factory():
        calls.append(1)
        return None

    assert await store.remember("miss", ttl=60, factory=factory) is None
    assert await store.remember("miss", ttl=60, factory=factory) is None
    assert len(calls) == 1


async def test_memory_explicitly_cached_none_is_a_hit():
    store = MemoryCache()
    await store.put("nil", None)
    calls = []

    def factory():
        calls.append(1)
        return "recomputed"

    assert await store.remember("nil", ttl=60, factory=factory) is None
    assert calls == []


async def test_redis_remember_caches_a_none_result():
    client = FakeRedis()
    store = RedisCache(client=client)
    calls = []

    async def factory():
        calls.append(1)
        return None

    assert await store.remember("nil", ttl=60, factory=factory) is None
    assert await store.remember("nil", ttl=60, factory=factory) is None
    assert len(calls) == 1


async def test_remember_coerces_a_textual_configured_ttl(monkeypatch):
    """CACHE_TTL can arrive as text — a bare env value keeps its string form
    when no config-module default is loaded (Config coerces against the
    default's type, and there is none). remember() must not TypeError deep
    in put() over it."""
    monkeypatch.setattr("fastplace.cache.config", lambda key, default=None: "3600")
    store = MemoryCache()
    clock = {"now": 100.0}
    monkeypatch.setattr("fastplace.cache.monotonic", lambda: clock["now"])

    await store.remember("txt", factory=lambda: "v")
    assert store._entries["txt"].deadline == 100.0 + 3600


async def test_non_numeric_ttl_fails_with_a_clear_error():
    # '<=' between str and int is a TypeError nobody can act on — the same
    # "fail fast with a clear message" contract as non-positive ttls.
    with pytest.raises(ValueError, match="cache ttl must be a positive number"):
        await MemoryCache().put("k", "v", ttl="soon")


async def test_put_rejects_non_positive_ttl():
    # redis rejects ex<=0 server-side with a cryptic error and a ttl of 0
    # seconds is always a bug — fail fast with a clear message instead.
    for bad_ttl in (0, -5):
        with pytest.raises(ValueError, match="ttl"):
            await MemoryCache().put("k", "v", ttl=bad_ttl)
        with pytest.raises(ValueError, match="ttl"):
            await RedisCache(client=FakeRedis()).put("k", "v", ttl=bad_ttl)


async def test_remember_without_ttl_uses_configured_cache_ttl(monkeypatch):
    monkeypatch.setenv("CACHE_TTL", "120")
    store = MemoryCache()
    clock = {"now": 100.0}
    monkeypatch.setattr("fastplace.cache.monotonic", lambda: clock["now"])

    async def factory():
        return "derived"

    await store.remember("cfg", factory=factory)
    entry = store._entries["cfg"]
    assert entry.deadline == 100.0 + 120


async def test_remember_with_explicit_ttl_wins_over_config(monkeypatch):
    monkeypatch.setenv("CACHE_TTL", "120")
    store = MemoryCache()
    clock = {"now": 100.0}
    monkeypatch.setattr("fastplace.cache.monotonic", lambda: clock["now"])

    await store.remember("cfg", ttl=30, factory=lambda: "v")
    assert store._entries["cfg"].deadline == 100.0 + 30


async def test_redis_put_of_unserializable_value_raises_a_clear_error():
    import datetime

    from fastplace.errors import FastplaceError

    store = RedisCache(client=FakeRedis())
    with pytest.raises(FastplaceError, match="datetime"):
        await store.put("when", datetime.datetime.now(tz=datetime.UTC))


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------


async def test_factory_defaults_to_memory(monkeypatch):
    monkeypatch.delenv("CACHE_DRIVER", raising=False)
    assert isinstance(cache(), MemoryCache)


async def test_factory_returns_singleton_per_process(monkeypatch):
    monkeypatch.delenv("CACHE_DRIVER", raising=False)
    assert cache() is cache()


async def test_factory_selects_redis_driver(monkeypatch):
    monkeypatch.setenv("CACHE_DRIVER", "redis")
    assert isinstance(cache(), RedisCache)


async def test_factory_selects_database_driver(monkeypatch):
    monkeypatch.setenv("CACHE_DRIVER", "database")
    assert isinstance(cache(), DatabaseCache)


async def test_factory_rejects_unknown_driver(monkeypatch):
    monkeypatch.setenv("CACHE_DRIVER", "memcached")
    with pytest.raises(Exception, match="memcached"):
        cache()


async def test_factory_rejects_redis_driver_without_library(monkeypatch):
    monkeypatch.setenv("CACHE_DRIVER", "redis")
    monkeypatch.setattr("fastplace.cache._redis_available", lambda: False)
    with pytest.raises(Exception, match="redis"):
        cache()
