"""MemorySessionStore — lazy expiry, injectable clock (spec §4.1)."""

from __future__ import annotations

from fastplace.http.session.memory import MemorySessionStore


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_write_then_read_roundtrips_payload():
    store = MemorySessionStore(lifetime=600)
    await store.write("sid-1", {"_token": "abc", "user_id": 7}, user_id=7)
    stored = await store.read("sid-1")
    assert stored is not None
    assert stored.payload == {"_token": "abc", "user_id": 7}
    assert stored.last_activity > 0


async def test_read_missing_session_returns_none():
    store = MemorySessionStore()
    assert await store.read("nope") is None


async def test_expired_session_reads_as_missing():
    clock = FakeClock()
    store = MemorySessionStore(lifetime=100, clock=clock)
    await store.write("sid-1", {"k": 1})
    clock.advance(100)  # exactly at the deadline -> expired
    assert await store.read("sid-1") is None


async def test_destroy_removes_the_row():
    store = MemorySessionStore()
    await store.write("sid-1", {"k": 1})
    await store.destroy("sid-1")
    assert await store.read("sid-1") is None
    await store.destroy("sid-1")  # idempotent


async def test_gc_sweeps_only_stale_rows():
    clock = FakeClock()
    store = MemorySessionStore(lifetime=100, clock=clock)
    await store.write("old", {"k": 1})
    clock.advance(200)
    await store.write("new", {"k": 2})
    removed = await store.gc()
    assert removed == 1
    assert await store.read("old") is None
    assert (await store.read("new")) is not None


async def test_read_returns_a_copy_not_the_stored_dict():
    store = MemorySessionStore()
    await store.write("sid-1", {"k": 1})
    first = await store.read("sid-1")
    assert first is not None
    first.payload["k"] = 999  # must not leak into the store
    second = await store.read("sid-1")
    assert second is not None
    assert second.payload["k"] == 1
