"""Task 28 — the restart sentinel + queue depth: driver-level contract.

The sentinel is a timestamped value on the ``cache()`` store under
``fastplace:queue:restart`` (any cache driver — the value is a JSON-safe ISO
string). The memory drain polls it BEFORE each job and stops, handing un-run
jobs back for the replacement worker; consuming the sentinel is the exiting
worker's job, not the drain's (the CLI consumes it for the memory driver, the
saq before_process hook for saq). ``queue_depth()`` is the waiting-jobs count
every monitor reads — MemoryQueue counts its deque, SaqQueue the installed
saq's ``count("queued")`` (0.26.4 has no ``stats()``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Fresh cache store per test — the sentinel key must never leak."""
    from fastplace.cache import reset_cache

    reset_cache()
    yield
    reset_cache()


@pytest.fixture(autouse=True)
def _fresh_queue():
    """Registry and queue singleton never leak between tests."""
    from fastplace.queue import reset_queue, reset_registry

    reset_registry()
    reset_queue()
    yield
    reset_registry()
    reset_queue()


# ---------------------------------------------------------------------------
# sentinel set / read / clear — the timestamped roundtrip
# ---------------------------------------------------------------------------


async def test_restart_sentinel_roundtrip():
    from fastplace.queue import clear_restart_sentinel, restart_requested_at, set_restart_sentinel

    assert await restart_requested_at() is None  # nothing requested yet

    await set_restart_sentinel()
    requested = await restart_requested_at()
    assert isinstance(requested, datetime)
    # Naive UTC, just now — the failed-job ledger's timestamp convention.
    from fastplace.queue_failures import utcnow

    assert abs((utcnow() - requested).total_seconds()) < 10

    await clear_restart_sentinel()
    assert await restart_requested_at() is None


async def test_sentinel_value_is_json_safe():
    """The store carries a plain ISO string — redis/database drivers serialize
    to JSON, so a datetime object would raise at put() time."""
    from fastplace.cache import cache
    from fastplace.queue import set_restart_sentinel

    await set_restart_sentinel()
    raw = await cache().get("fastplace:queue:restart")
    assert isinstance(raw, str)
    datetime.fromisoformat(raw)  # parses as an ISO timestamp


async def test_corrupt_sentinel_reads_as_none():
    """A garbage value must read as 'no restart' (workers stay up — the
    availability-safe direction) instead of crash-looping the drain."""
    from fastplace.cache import cache
    from fastplace.queue import restart_requested_at

    await cache().put("fastplace:queue:restart", "not-a-timestamp")
    assert await restart_requested_at() is None


async def test_sentinel_read_survives_a_broken_cache(monkeypatch):
    """A cache outage on the read path must never take the drain down with
    it — the restart protocol is best-effort (the broken-store precedent)."""
    import fastplace.cache as cache_module
    from fastplace.queue import restart_requested_at

    class _BrokenCache:
        async def get(self, key: str):
            raise RuntimeError("cache down")

    monkeypatch.setattr(cache_module, "cache", lambda: _BrokenCache())
    assert await restart_requested_at() is None


# ---------------------------------------------------------------------------
# memory drain — stops at the sentinel, jobs preserved, sentinel left for
# the exiting worker to consume
# ---------------------------------------------------------------------------


async def test_memory_drain_stops_at_pre_set_sentinel():
    """The brief's canonical case: sentinel pre-set, one pending job — the
    job must NOT run, must stay queued, and the drain must not consume the
    sentinel (that is the exiting worker's job)."""
    from fastplace.queue import Job, MemoryQueue, restart_requested_at, set_restart_sentinel

    ran: list[int] = []

    @Job(name="t28_stop_before")
    async def probe(n: int) -> None:
        ran.append(n)

    await set_restart_sentinel()
    mem = MemoryQueue()
    await mem.dispatch("t28_stop_before", n=1)
    await mem.dispatch("t28_stop_before", n=2)

    executed = await mem.run_pending()
    assert executed == 0
    assert ran == []
    assert len(mem.pending) == 2  # both stay queued for the replacement worker
    assert await restart_requested_at() is not None  # the drain only observes


async def test_memory_drain_checks_the_sentinel_between_jobs():
    """The check runs BEFORE each job: a sentinel raised by job 1's own side
    effect stops job 2 (and puts it back), while job 1's work stays done."""
    from fastplace.queue import Job, MemoryQueue, set_restart_sentinel

    ran: list[str] = []

    @Job(name="t28_raises_restart")
    async def raises_restart() -> None:
        ran.append("first")
        await set_restart_sentinel()

    @Job(name="t28_after_restart")
    async def after_restart() -> None:
        ran.append("second")

    mem = MemoryQueue()
    await mem.dispatch("t28_raises_restart")
    await mem.dispatch("t28_after_restart")

    executed = await mem.run_pending()
    assert executed == 1
    assert ran == ["first"]
    assert [item.name for item in mem.pending] == ["t28_after_restart"]
    assert mem.failures == []  # stopping for a restart is not a failure


async def test_memory_drain_restores_jobs_in_order():
    """The restored block keeps its dispatch order — extendleft(reversed(...))
    must not flip the queue."""
    from fastplace.queue import Job, MemoryQueue, set_restart_sentinel

    @Job(name="t28_order_blocker")
    async def blocker() -> None:
        await set_restart_sentinel()

    @Job(name="t28_order_first")
    async def first() -> None: ...

    @Job(name="t28_order_second")
    async def second() -> None: ...

    mem = MemoryQueue()
    await mem.dispatch("t28_order_blocker")
    await mem.dispatch("t28_order_first")
    await mem.dispatch("t28_order_second")

    await mem.run_pending()
    # Job 1 ran (and requested the restart); jobs 2 and 3 are handed back in
    # their original dispatch order.
    assert [item.name for item in mem.pending] == ["t28_order_first", "t28_order_second"]


# ---------------------------------------------------------------------------
# queue_depth — the waiting-jobs count on every driver
# ---------------------------------------------------------------------------


async def test_memory_queue_depth_counts_pending():
    from fastplace.queue import Job, MemoryQueue

    @Job(name="t28_depth_probe")
    async def probe() -> None:
        return None

    mem = MemoryQueue()
    assert await mem.queue_depth() == 0
    await mem.dispatch("t28_depth_probe")
    await mem.dispatch("t28_depth_probe")
    assert await mem.queue_depth() == 2
    await mem.run_pending()
    assert await mem.queue_depth() == 0


async def test_saq_queue_depth_uses_installed_saq_count():
    """Installed saq (0.26.4) exposes no stats() — the depth is the public
    ``count("queued")`` (one llen of the queue list)."""
    from fastplace.queue import SaqQueue

    class FakeSaqQueue:
        def __init__(self) -> None:
            self.counted: list[str] = []

        async def count(self, kind: str) -> int:
            self.counted.append(kind)
            return 4 if kind == "queued" else 99

    fake = FakeSaqQueue()
    driver = SaqQueue(queue=fake)
    assert await driver.queue_depth() == 4
    assert fake.counted == ["queued"]


def test_queue_driver_protocol_includes_queue_depth():
    """A fake driver with dispatch + clear + queue_depth satisfies the
    protocol; both shipped drivers implement the whole surface."""
    from fastplace.queue import MemoryQueue, QueueDriver, SaqQueue

    assert hasattr(MemoryQueue(), "queue_depth")
    assert hasattr(SaqQueue(), "queue_depth")

    class FakeDriver:
        async def dispatch(self, name: str, **kwargs: object) -> None: ...

        async def clear(self) -> int:
            return 0

        async def queue_depth(self) -> int:
            return 7

    driver: QueueDriver = FakeDriver()  # structural conformance
    assert asyncio.run(driver.queue_depth()) == 7


# ---------------------------------------------------------------------------
# the saq before_process hook — honor, clear, and stop at a job boundary
# ---------------------------------------------------------------------------


async def test_saq_restart_hook_honors_the_sentinel():
    """With the sentinel set: the dequeued job goes back (retry — the
    replacement worker picks it up), the worker's stop event fires, the
    sentinel is consumed, and the hook cancels the current job (installed
    saq's process() treats CancelledError before the task exists as a clean
    skip — the job is not failed, not run)."""
    from fastplace.queue import _check_restart_sentinel, restart_requested_at, set_restart_sentinel

    retried: list[str] = []

    class FakeJob:
        async def retry(self, error: str | None) -> None:
            retried.append(error)

    class FakeWorker:
        def __init__(self) -> None:
            self.event = asyncio.Event()

    await set_restart_sentinel()
    worker = FakeWorker()
    ctx: dict = {"job": FakeJob(), "worker": worker}

    with pytest.raises(asyncio.CancelledError):
        await _check_restart_sentinel(ctx)

    assert retried == ["worker restart requested"]
    assert worker.event.is_set()
    assert await restart_requested_at() is None  # consumed — no exit loop


async def test_saq_restart_hook_is_a_noop_without_the_sentinel():
    from fastplace.queue import _check_restart_sentinel

    class FakeJob:
        async def retry(self, error: str | None) -> None:
            raise AssertionError("an uncontested sentinel must never re-queue a job")

    worker_event = asyncio.Event()
    ctx: dict = {"job": FakeJob(), "worker": type("W", (), {"event": worker_event})()}

    await _check_restart_sentinel(ctx)  # returns quietly
    assert not worker_event.is_set()


def test_saq_build_worker_carries_the_restart_hook():
    """The CLI's worker always polls the sentinel — the hook is installed by
    build_worker itself, so no caller can forget it. Installed saq passes
    async hooks through unwrapped, so identity survives the worker build."""
    from fastplace.queue import Job, SaqQueue, _check_restart_sentinel

    @Job(name="t28_hook_probe")
    async def probe() -> None:
        return None

    driver = SaqQueue(url="redis://localhost:6379/2", name="fastplace")
    worker = driver.build_worker()
    assert _check_restart_sentinel in (worker.before_process or [])
