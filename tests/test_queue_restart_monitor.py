"""Task 28 — the restart sentinel + queue depth: driver-level contract.

The sentinel is a timestamped value on the ``cache()`` store under
``fastplace:queue:restart`` (any cache driver — the value is a JSON-safe ISO
string). The memory drain polls it BEFORE each job and stops, handing un-run
jobs back for the replacement worker; consuming the sentinel is the exiting
worker's job, not the drain's (the CLI consumes it for the memory driver, the
saq after_process hook for saq). ``queue_depth()`` is the waiting-jobs count
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


async def test_memory_drain_puts_deferred_jobs_ahead_of_the_restored_block():
    """Review F14: a batch holding a not-yet-due delayed dispatch plus due
    jobs, stopped mid-drain by a sentinel, must hand back deferred FIRST
    (they were earlier in the batch) and the un-run due jobs behind them in
    dispatch order — the replacement worker re-evaluates the same sequence
    the original drain saw."""
    from fastplace.queue import Job, MemoryQueue, set_restart_sentinel

    @Job(name="t28_deferred_probe")
    async def deferred_probe(n: int) -> None: ...

    @Job(name="t28_deferred_blocker")
    async def blocker() -> None:
        await set_restart_sentinel()

    mem = MemoryQueue()
    await mem.job("t28_deferred_probe", delay=300).dispatch(n=1)  # not due
    await mem.dispatch("t28_deferred_blocker")
    await mem.job("t28_deferred_probe").dispatch(n=2)  # due, after the blocker
    await mem.job("t28_deferred_probe").dispatch(n=3)  # due, never reached

    await mem.run_pending()

    names = [item.name for item in mem.pending]
    kwargs = [item.kwargs["n"] for item in mem.pending if item.name == "t28_deferred_probe"]
    # The delayed dispatch leads the restored block; the un-run due jobs keep
    # their dispatch order behind it.
    assert names == ["t28_deferred_probe", "t28_deferred_probe", "t28_deferred_probe"]
    assert kwargs == [1, 2, 3]


# ---------------------------------------------------------------------------
# honor_sentinel=False — consumers that never consume the sentinel
# (the kernel's shutdown drain is the canonical case: the web process is
# not a restartable worker, so a latched sentinel must not eat its jobs)
# ---------------------------------------------------------------------------


async def test_run_pending_can_ignore_the_sentinel():
    """A caller that will never consume the sentinel (the kernel shutdown
    drain) drains right through one: jobs run, none are restored, and the
    sentinel itself is left latched."""
    from fastplace.queue import Job, MemoryQueue, restart_requested_at, set_restart_sentinel

    ran: list[int] = []

    @Job(name="t28_ignore_sentinel")
    async def probe(n: int) -> None:
        ran.append(n)

    await set_restart_sentinel()
    mem = MemoryQueue()
    await mem.dispatch("t28_ignore_sentinel", n=1)
    await mem.dispatch("t28_ignore_sentinel", n=2)

    executed = await mem.run_pending(honor_sentinel=False)
    assert executed == 2
    assert ran == [1, 2]
    assert not mem.pending  # nothing restored for a replacement that never comes
    assert await restart_requested_at() is not None  # still latched


async def test_kernel_shutdown_drain_ignores_a_latched_sentinel():
    """The third sentinel consumer (final review I-3): a shared-cache sentinel
    latched by `queue:restart` must not silently disable the web process's
    shutdown drain — pending jobs still run, sentinel untouched."""
    from fastplace.http.kernel import _drain_memory_queue_on_shutdown
    from fastplace.queue import Job, queue, restart_requested_at, set_restart_sentinel

    ran: list[bool] = []

    @Job(name="t28_kernel_drain_job")
    async def probe() -> None:
        ran.append(True)

    q = queue()  # the same singleton the kernel drain runs
    await q.dispatch("t28_kernel_drain_job")
    await set_restart_sentinel()

    await _drain_memory_queue_on_shutdown()

    assert ran == [True]  # the job ran despite the sentinel
    assert not q.pending
    assert await restart_requested_at() is not None  # the drain only observes


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
# the saq after_process hook — honor, clear, and stop at a job boundary
# ---------------------------------------------------------------------------


async def test_saq_restart_hook_honors_the_sentinel():
    """With the sentinel set: the worker's stop event fires (the in-flight
    job finishes, no new ones start), the sentinel is consumed, and the
    dequeued job RUNS TO COMPLETION exactly once — no retry, no
    CancelledError. The old cancel-and-retry shape left the job ACTIVE (a
    pre-task CancelledError returns False from process()) for the sweeper
    to abort ~90s later: the silent-loss path this contract forbids."""
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

    await _check_restart_sentinel(ctx)  # returns quietly — the job then runs

    assert retried == []  # exactly once — the boundary job is never re-queued
    assert worker.event.is_set()
    assert await restart_requested_at() is None  # consumed — no exit loop


async def test_saq_restart_hook_survives_a_failed_sentinel_clear(monkeypatch, caplog):
    """A cache outage while clearing must not cancel the in-flight job (the
    loss path this wave closes) — the hook stops the worker, leaves the
    sentinel latched, and the CLI exit path turns the latched sentinel into
    a non-zero exit."""
    import fastplace.cache as cache_module
    from fastplace.queue import _check_restart_sentinel, set_restart_sentinel

    class _BrokenForgetCache:
        async def get(self, key: str):
            return "2026-01-01T00:00:00"  # sentinel visible

        async def forget(self, key: str):
            raise RuntimeError("cache down")

    await set_restart_sentinel()  # latches via the real store first
    monkeypatch.setattr(cache_module, "cache", lambda: _BrokenForgetCache())

    class FakeJob:
        async def retry(self, error: str | None) -> None:
            raise AssertionError("restart must never re-queue the boundary job")

    worker_event = asyncio.Event()

    class FakeWorker:
        event = worker_event

    with caplog.at_level("ERROR", logger="fastplace.queue"):
        ctx = {"job": FakeJob(), "worker": FakeWorker()}
        await _check_restart_sentinel(ctx)  # must not raise, must not cancel

    assert worker_event.is_set()  # still exits — replacement path stays live
    assert any("clear failed" in message for message in caplog.messages)


async def test_clear_restart_sentinel_is_strict_about_a_broken_cache(monkeypatch):
    """Consumption failure must be loud: swallowing it would let a worker
    report a clean restart while the sentinel stays latched — and the
    replacement worker would immediately exit again, an outage masked as
    success. The CLI exit path relies on this raise to exit non-zero."""
    import fastplace.cache as cache_module
    from fastplace.queue import clear_restart_sentinel

    class _BrokenCache:
        async def forget(self, key: str) -> None:
            raise RuntimeError("cache down")

    monkeypatch.setattr(cache_module, "cache", lambda: _BrokenCache())
    with pytest.raises(RuntimeError, match="cache down"):
        await clear_restart_sentinel()


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
    build_worker itself (as an after_process hook: the post-job placement is
    what stops the worker without cancelling the boundary job), so no caller
    can forget it. Installed saq passes async hooks through unwrapped, so
    identity survives the worker build."""
    from fastplace.queue import Job, SaqQueue, _check_restart_sentinel, _record_saq_failure

    @Job(name="t28_hook_probe")
    async def probe() -> None:
        return None

    driver = SaqQueue(url="redis://localhost:6379/2", name="fastplace")
    worker = driver.build_worker()
    hooks = worker.after_process or []
    assert _check_restart_sentinel in hooks
    # Failure recording still rides along, ordered before the restart check.
    assert _record_saq_failure in hooks
    assert hooks.index(_record_saq_failure) < hooks.index(_check_restart_sentinel)
