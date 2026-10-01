"""plat-G2 — atomic cache locks: one API on the store, every driver.

The locking CONTRACT lives in CacheLock (acquire/release/block/context
manager); each driver supplies only its atomic claim primitive. Redis
release must be a server-side compare-and-delete (Lua eval), never a
GET-then-DELETE — the fake asserts the script shape so a two-step
regression fails loudly here rather than silently in production.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from fastplace.cache import (
    _RELEASE_SCRIPT,
    CacheLock,
    CacheStore,
    DatabaseCache,
    LockTimeout,
    MemoryCache,
    RedisCache,
    _cache_locks_table,
)
from fastplace.errors import FastplaceError

# ---------------------------------------------------------------------------
# MemoryCache — dict + monotonic deadline, atomic on the event loop
# ---------------------------------------------------------------------------


async def test_memory_lock_second_acquire_while_held_is_refused():
    store = MemoryCache()
    first = store.lock("job", ttl=30)
    second = store.lock("job", ttl=30)
    assert await first.acquire() is True
    assert await second.acquire() is False


async def test_memory_lock_release_frees_the_name():
    store = MemoryCache()
    first = store.lock("job", ttl=30)
    await first.acquire()
    assert await first.release() is True
    assert await store.lock("job", ttl=30).acquire() is True


async def test_memory_lock_release_is_owner_checked():
    # A handle that never won the name must not be able to drop the holder.
    store = MemoryCache()
    holder = store.lock("job", ttl=30)
    await holder.acquire()
    stranger = store.lock("job", ttl=30)
    assert await stranger.release() is False
    assert await store.lock("job", ttl=30).acquire() is False  # still held


async def test_memory_lock_expires_and_is_reclaimed(monkeypatch):
    store = MemoryCache()
    clock = {"now": 0.0}
    monkeypatch.setattr("fastplace.cache.monotonic", lambda: clock["now"])

    dead = store.lock("job", ttl=10)
    assert await dead.acquire() is True
    clock["now"] = 10.5  # holder died — ttl bounds the damage

    assert await store.lock("job", ttl=10).acquire() is True


async def test_memory_lock_release_after_reclaim_is_a_noop(monkeypatch):
    # The classic expiry race: A's lock expired and C reclaimed it — A's stale
    # release must not delete C's lock.
    store = MemoryCache()
    clock = {"now": 0.0}
    monkeypatch.setattr("fastplace.cache.monotonic", lambda: clock["now"])

    stale = store.lock("job", ttl=10)
    await stale.acquire()
    clock["now"] = 11.0
    current = store.lock("job", ttl=10)
    assert await current.acquire() is True

    assert await stale.release() is False
    assert await store.lock("job", ttl=10).acquire() is False  # current intact


async def test_memory_lock_is_not_reentrant():
    # One token, one hold — re-acquiring on a held handle fails everywhere.
    store = MemoryCache()
    lock = store.lock("job", ttl=30)
    assert await lock.acquire() is True
    assert await lock.acquire() is False


async def test_memory_lock_block_returns_once_released():
    store = MemoryCache()
    holder = store.lock("job", ttl=30)
    await holder.acquire()

    async def releaser() -> None:
        await asyncio.sleep(0.02)
        await holder.release()

    waiter = store.lock("job", ttl=30)
    await asyncio.wait_for(asyncio.gather(releaser(), waiter.block(timeout=5)), timeout=5)
    assert waiter._held is True  # block() ends holding the lock


async def test_memory_lock_block_without_timeout_waits_until_release():
    store = MemoryCache()
    holder = store.lock("job", ttl=30)
    await holder.acquire()
    waiter = store.lock("job", ttl=30)

    async def releaser() -> None:
        await asyncio.sleep(0.02)
        await holder.release()

    blocking = asyncio.create_task(waiter.block())  # timeout=None waits forever
    await asyncio.sleep(0.01)
    assert not blocking.done()
    await releaser()
    await asyncio.wait_for(blocking, timeout=5)


async def test_memory_lock_block_raises_lock_timeout_on_deadline():
    store = MemoryCache()
    await store.lock("job", ttl=30).acquire()
    with pytest.raises(LockTimeout, match="job"):
        await store.lock("job", ttl=30).block(timeout=0.02)


async def test_memory_lock_context_manager_acquires_then_releases():
    store = MemoryCache()
    async with store.lock("job", ttl=30):
        assert await store.lock("job", ttl=30).acquire() is False  # held inside
    assert await store.lock("job", ttl=30).acquire() is True  # freed on exit


async def test_memory_lock_context_manager_waits_for_the_holder():
    store = MemoryCache()
    holder = store.lock("job", ttl=30)
    await holder.acquire()
    events: list[str] = []

    async def releaser() -> None:
        await asyncio.sleep(0.02)
        events.append("released")
        await holder.release()

    async def enter_later() -> CacheLock:
        async with store.lock("job", ttl=30) as lock:
            events.append("entered")
            return lock

    await asyncio.wait_for(asyncio.gather(releaser(), enter_later()), timeout=5)
    # The waiter genuinely blocked on the holder: entry is observable only
    # after the release — not merely "both coroutines finished in time".
    assert events == ["released", "entered"]


async def test_memory_lock_race_admits_exactly_one_holder():
    store = MemoryCache()
    handles = [store.lock("contended", ttl=30) for _ in range(25)]
    results = await asyncio.gather(*(lock.acquire() for lock in handles))
    assert results.count(True) == 1


async def test_memory_lock_rejects_bad_ttl():
    store = MemoryCache()
    for bad_ttl in (0, -5, "soon"):
        with pytest.raises(ValueError, match="ttl"):
            store.lock("job", ttl=bad_ttl)  # type: ignore[arg-type]


async def test_memory_lock_rejects_non_finite_ttl():
    # inf survives the <= check and dies later as an OverflowError from
    # quantization; nan slips the <= check entirely — both are ttl bugs and
    # must fail here with the contract's message, on every driver's path.
    store = MemoryCache()
    for bad_ttl in (float("inf"), float("nan")):
        with pytest.raises(ValueError, match="finite"):
            store.lock("job", ttl=bad_ttl)  # type: ignore[arg-type]


async def test_memory_lock_requires_a_ttl():
    # An unbounded lock deadlocks forever when its holder dies — ttl is part
    # of the contract, not an option.
    with pytest.raises(TypeError):
        MemoryCache().lock("job")  # type: ignore[call-arg]


async def test_memory_lock_ttl_quantizes_to_whole_seconds():
    # Database rows store unix seconds; every driver must bound the lock for
    # the same horizon, so lock ttls quantize up at the store boundary.
    lock = MemoryCache().lock("job", ttl=1.5)
    assert lock.ttl == 2


async def test_memory_flush_keeps_live_locks():
    # flush() forgets cached keys, never coordination state — a live lock
    # silently losing mutual exclusion mid-section would be worse than any
    # stale lock. Every driver keeps locks across flush.
    store = MemoryCache()
    lock = store.lock("job", ttl=30)
    await lock.acquire()
    await store.put("k", "v")
    await store.flush()
    assert await store.get("k") is None
    assert await store.lock("job", ttl=30).acquire() is False  # still held
    assert await lock.release() is True


# ---------------------------------------------------------------------------
# RedisCache — SET NX EX acquire, Lua compare-and-delete release
# ---------------------------------------------------------------------------


class FakeLockRedis:
    """Async redis stub covering exactly the two commands locks use.

    ``eval`` is the redis protocol command (EVAL — the server executes the
    Lua script), not Python's eval; the stub mirrors the server's
    compare-and-delete semantics and refuses any script other than the
    release script, so a GET-then-DELETE regression cannot pass these tests.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.commands: list[tuple] = []

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        self.commands.append(("set", key, value, ex, nx))
        if nx and key in self.store:
            return None  # redis: NX loses, no write
        self.store[key] = value
        return True

    async def get(self, key: str):
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def eval(self, script: str, numkeys: int, key: str, arg: str):
        self.commands.append(("eval", numkeys, key, arg))
        assert script.strip() == _RELEASE_SCRIPT.strip(), (
            "lock release must be an atomic server-side compare-and-delete"
        )
        if self.store.get(key) == arg:
            del self.store[key]
            return 1
        return 0

    async def scan_iter(self, match: str | None = None):
        # redis-py default (decode_responses=False) serves SCAN hits as bytes —
        # the fake must too, or a bytes-handling bug in flush() hides here.
        import fnmatch

        for key in list(self.store):
            if match is None or fnmatch.fnmatch(key, match):
                yield key.encode()

    async def unlink(self, *keys: bytes | str) -> None:
        for key in keys:
            self.store.pop(key.decode() if isinstance(key, bytes) else key, None)


async def test_redis_lock_acquire_uses_set_nx_ex():
    client = FakeLockRedis()
    await RedisCache(client=client).lock("job", ttl=30).acquire()
    assert client.commands == [
        ("set", "fastplace:cache:locks:job", client.store["fastplace:cache:locks:job"], 30, True)
    ]


async def test_redis_lock_lost_race_returns_false():
    client = FakeLockRedis()
    store = RedisCache(client=client)
    assert await store.lock("job", ttl=30).acquire() is True
    assert await store.lock("job", ttl=30).acquire() is False


async def test_redis_lock_release_deletes_only_the_own_token():
    client = FakeLockRedis()
    store = RedisCache(client=client)
    holder = store.lock("job", ttl=30)
    await holder.acquire()

    assert await store.lock("job", ttl=30).release() is False  # stranger: no-op
    assert "fastplace:cache:locks:job" in client.store

    assert await holder.release() is True
    assert "fastplace:cache:locks:job" not in client.store
    assert await holder.release() is False  # double release: no-op


async def test_redis_lock_keys_carry_the_cache_prefix(monkeypatch):
    monkeypatch.setenv("CACHE_PREFIX", "myapp:cache:")
    client = FakeLockRedis()
    await RedisCache(client=client).lock("job", ttl=30).acquire()
    assert list(client.store) == ["myapp:cache:locks:job"]


async def test_redis_flush_preserves_locks():
    # Same contract as memory/database: cached keys go, live locks stay.
    client = FakeLockRedis()
    store = RedisCache(client=client)
    lock = store.lock("job", ttl=30)
    await lock.acquire()
    await store.put("k", "v")
    await store.flush()
    assert await store.get("k") is None
    assert "fastplace:cache:locks:job" in client.store
    assert await lock.release() is True


async def test_redis_lock_rejects_bad_ttl():
    with pytest.raises(ValueError, match="ttl"):
        RedisCache(client=FakeLockRedis()).lock("job", ttl=0)


# ---------------------------------------------------------------------------
# DatabaseCache — portable claim on a dedicated locks table
# ---------------------------------------------------------------------------


@pytest.fixture
async def _db_lock_store(monkeypatch: pytest.MonkeyPatch, tmp_path) -> AsyncIterator[DatabaseCache]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/locks.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    from fastplace.db import reset_db

    reset_db()
    yield DatabaseCache()
    reset_db()


async def test_database_lock_acquire_release_roundtrip(_db_lock_store):
    lock = _db_lock_store.lock("job", ttl=30)
    assert await lock.acquire() is True
    assert await lock.release() is True
    assert await _db_lock_store.lock("job", ttl=30).acquire() is True


async def test_database_lock_is_exclusive(_db_lock_store):
    store = _db_lock_store
    assert await store.lock("job", ttl=30).acquire() is True
    assert await store.lock("job", ttl=30).acquire() is False


async def test_database_lock_reclaims_an_expired_row(_db_lock_store):
    from sqlalchemy import update

    from fastplace.db import db

    store = _db_lock_store
    dead = store.lock("job", ttl=30)
    await dead.acquire()

    engine = db.manager.engine("default")
    async with engine.begin() as conn:  # the holder died — force expiry
        await conn.execute(
            update(_cache_locks_table)
            .where(_cache_locks_table.c.name == store._key("job"))
            .values(expires_at=1)
        )

    assert await store.lock("job", ttl=30).acquire() is True


async def test_database_lock_is_not_stealable_before_the_full_ttl(_db_lock_store, monkeypatch):
    # expires_at = floor(now) + ttl can lapse up to a second before the ttl
    # elapses (memory/redis hold the full ttl) — quantize the acquire instant
    # up so the row stays unstealable for the whole horizon it was given.
    clock = {"now": 100.9}
    monkeypatch.setattr("fastplace.cache.time", SimpleNamespace(time=lambda: clock["now"]))

    store = _db_lock_store
    assert await store.lock("job", ttl=30).acquire() is True

    clock["now"] = 130.05  # a tick before the full ttl — still held
    assert await store.lock("job", ttl=30).acquire() is False

    clock["now"] = 131.0  # ttl fully elapsed — the rival reclaims
    assert await store.lock("job", ttl=30).acquire() is True


async def test_database_lock_release_is_owner_checked(_db_lock_store):
    store = _db_lock_store
    holder = store.lock("job", ttl=30)
    await holder.acquire()

    assert await store.lock("job", ttl=30).release() is False  # stranger: no-op
    assert await store.lock("job", ttl=30).acquire() is False  # still held
    assert await holder.release() is True
    assert await holder.release() is False  # double release: no-op


async def test_database_lock_release_after_reclaim_is_a_noop(_db_lock_store):
    # The classic expiry race, on the row-based driver: A's row expired and C
    # reclaimed it — A's stale release must not delete C's row.
    from sqlalchemy import update

    from fastplace.db import db

    store = _db_lock_store
    stale = store.lock("job", ttl=30)
    await stale.acquire()

    engine = db.manager.engine("default")
    async with engine.begin() as conn:  # the holder died — force expiry
        await conn.execute(
            update(_cache_locks_table)
            .where(_cache_locks_table.c.name == store._key("job"))
            .values(expires_at=1)
        )

    current = store.lock("job", ttl=30)
    assert await current.acquire() is True

    assert await stale.release() is False
    assert await store.lock("job", ttl=30).acquire() is False  # current intact


async def test_database_lock_race_admits_exactly_one_holder(_db_lock_store):
    store = _db_lock_store
    results = await asyncio.gather(
        store.lock("contended", ttl=30).acquire(),
        store.lock("contended", ttl=30).acquire(),
    )
    assert results.count(True) == 1


async def test_database_lock_block_raises_lock_timeout(_db_lock_store):
    store = _db_lock_store
    await store.lock("job", ttl=30).acquire()
    with pytest.raises(LockTimeout, match="job"):
        await store.lock("job", ttl=30).block(timeout=0.02)


async def test_database_lock_block_returns_once_released(_db_lock_store):
    store = _db_lock_store
    holder = store.lock("job", ttl=30)
    await holder.acquire()

    async def releaser() -> None:
        await asyncio.sleep(0.02)
        await holder.release()

    waiter = store.lock("job", ttl=30)
    await asyncio.wait_for(asyncio.gather(releaser(), waiter.block(timeout=5)), timeout=5)
    assert waiter._held is True  # block() ends holding the lock


async def test_database_lock_context_manager_acquires_then_releases(_db_lock_store):
    store = _db_lock_store
    async with store.lock("job", ttl=30):
        assert await store.lock("job", ttl=30).acquire() is False
    assert await store.lock("job", ttl=30).acquire() is True


async def test_database_flush_keeps_live_locks(_db_lock_store):
    store = _db_lock_store
    lock = store.lock("job", ttl=30)
    await lock.acquire()
    await store.put("k", "v")
    await store.flush()
    assert await store.get("k") is None
    assert await store.lock("job", ttl=30).acquire() is False  # still held
    assert await lock.release() is True


async def test_database_locks_table_is_created_idempotently(_db_lock_store):
    # A second store instance (new process, new worker) must not collide with
    # the existing locks table — create_all(checkfirst=True).
    second = DatabaseCache()
    assert await second.lock("job", ttl=30).acquire() is True
    assert await _db_lock_store.lock("job", ttl=30).acquire() is False


async def test_database_purge_expired_sweeps_expired_lock_rows(_db_lock_store):
    # purge_expired is the explicit sweep — an expired lock row whose holder
    # died without releasing is dead weight the reclaim predicate never
    # revisits, so the sweep must clear it and spare a live holder.
    from sqlalchemy import insert, select

    from fastplace.db import db

    store = _db_lock_store
    engine = db.manager.engine("default")
    assert await store.lock("live", ttl=30).acquire() is True
    async with engine.begin() as conn:
        await conn.execute(
            insert(_cache_locks_table).values(
                name=store._key("dead"), token="dead-token", expires_at=1
            )
        )

    assert await store.purge_expired(now=1_000_000) == 1  # exactly the dead row

    async with engine.connect() as conn:
        names = [
            row.name
            for row in (
                await conn.execute(
                    select(_cache_locks_table.c.name).order_by(_cache_locks_table.c.name)
                )
            )
        ]
    assert names == [store._key("live")]


# ---------------------------------------------------------------------------
# contract — one surface, every driver
# ---------------------------------------------------------------------------


async def test_every_driver_satisfies_the_cache_store_protocol():
    assert isinstance(MemoryCache(), CacheStore)
    assert isinstance(RedisCache(client=FakeLockRedis()), CacheStore)
    assert isinstance(DatabaseCache(), CacheStore)


async def test_lock_timeout_is_a_framework_error():
    # Consumers can catch FastplaceError and get the whole family.
    assert issubclass(LockTimeout, FastplaceError)
