"""RedisSessionStore — hand-rolled FakeRedis stub, no fakeredis dep (spec §4.1)."""

from __future__ import annotations

from fastplace.http.session.redis_store import RedisSessionStore


class FakeRedis:
    """The three commands the store uses — tests/cache/test_cache.py precedent."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: dict[str, int | None] = {}
        self.commands: list[tuple] = []

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.commands.append(("set", key, ex))
        self.store[key] = value
        self.expires[key] = ex

    async def get(self, key: str) -> str | None:
        self.commands.append(("get", key))
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.commands.append(("delete", key))
        self.store.pop(key, None)
        self.expires.pop(key, None)


async def test_write_then_read_roundtrips_payload():
    redis = FakeRedis()
    store = RedisSessionStore(client=redis)
    await store.write("sid-1", {"_token": "abc", "user_id": 7}, user_id=7)
    stored = await store.read("sid-1")
    assert stored is not None
    assert stored.payload == {"_token": "abc", "user_id": 7}
    assert stored.last_activity > 0


async def test_write_uses_the_namespaced_key_and_lifetime_ttl():
    redis = FakeRedis()
    store = RedisSessionStore(client=redis)
    await store.write("sid-1", {"k": 1})
    (command, key, ex), *_ = [c for c in redis.commands if c[0] == "set"]
    assert command == "set"
    assert key == "fastplace:session:sid-1"
    assert ex == 7200  # SESSION_LIFETIME default rides the native TTL


async def test_read_missing_returns_none():
    store = RedisSessionStore(client=FakeRedis())
    assert await store.read("nope") is None


async def test_destroy_deletes_the_key():
    redis = FakeRedis()
    store = RedisSessionStore(client=redis)
    await store.write("sid-1", {"k": 1})
    await store.destroy("sid-1")
    assert await store.read("sid-1") is None
    assert "fastplace:session:sid-1" not in redis.store


async def test_gc_is_a_noop_redis_expires_keys_server_side():
    store = RedisSessionStore(client=FakeRedis())
    assert await store.gc() == 0
