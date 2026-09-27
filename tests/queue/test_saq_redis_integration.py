"""Real-redis saq integration — the Critical handler-shape proof.

These tests run only when a local redis answers on 6379. They use DB index 11
exclusively and a unique queue namespace per test (``fftest-w1-<uuid>``); each
test deletes only its own namespace's keys and job ids — never FLUSH, never a
neighboring DB. Skip (not fail) when redis is absent: the suite stays green on
machines without redis while the shape bug is proven where redis exists.
"""

from __future__ import annotations

import asyncio
import socket
import uuid

import pytest

from fastplace.queue import (
    Job,
    SaqQueue,
    reset_queue,
    restart_requested_at,
    reset_registry,
    set_restart_sentinel,
)

#: Shared local redis, DB index 11 only — the framework's live-probe lane.
REDIS_URL = "redis://localhost:6379/11"


def _redis_reachable(url: str = REDIS_URL, timeout: float = 0.5) -> bool:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        with socket.create_connection((parsed.hostname, parsed.port or 6379), timeout=timeout):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _redis_reachable(), reason="local redis on 6379 not reachable")


@pytest.fixture(autouse=True)
def _fresh_queue():
    reset_registry()
    reset_queue()
    yield
    reset_registry()
    reset_queue()


def _namespace() -> str:
    return f"fftest-w1-{uuid.uuid4().hex[:8]}"


async def _purge(driver: SaqQueue) -> None:
    """Delete this driver's own namespace keys + tracked job ids, disconnect."""
    q = driver.queue
    redis = q.redis
    tracked = await redis.zrange(q.namespace("incomplete"), 0, -1)
    keys = [
        q.namespace(suffix)
        for suffix in ("queued", "active", "incomplete", "schedule", "sweep", "stats")
    ]
    tracked = [job_id if isinstance(job_id, str) else job_id.decode() for job_id in tracked]
    keys.extend(tracked)
    if keys:
        await redis.delete(*keys)
    await q.disconnect()


async def test_kwarg_carrying_job_executes_on_real_saq_worker():
    """The Critical (q1-G1/q2-G1): a dispatched kwarg job must EXECUTE on a
    real saq Worker over real redis — not fail with TypeError because the
    worker passed its ctx dict into the handler."""
    ran: list[tuple[int, str]] = []
    driver = SaqQueue(url=REDIS_URL, name=_namespace())

    @Job(name="it.kwargs_probe")
    async def kwargs_probe(user_id: int, note: str):
        ran.append((user_id, note))
        return "ok"

    @Job(name="it.bare_probe")
    async def bare_probe():
        ran.append((-1, "bare"))

    try:
        await driver.dispatch("it.kwargs_probe", user_id=7, note="hello")
        await driver.dispatch("it.bare_probe")

        worker = driver.build_worker(burst=True, dequeue_timeout=0.5)
        await worker.start()  # burst: returns once the queue is drained

        assert (7, "hello") in ran
        assert (-1, "bare") in ran

        depth = await driver.queue_depth()
        assert depth == 0
    finally:
        await _purge(driver)


async def test_one_handler_shape_runs_unchanged_on_both_drivers():
    """Cross-driver conformance (q2-G7): ONE handler definition — the
    documented ``fn(**kwargs)`` shape — must execute identically on the
    memory driver and the saq driver with no per-driver edits."""
    from fastplace.queue import MemoryQueue

    ran: list[str] = []

    @Job(name="conf.shared_shape")
    async def shared(user_id: int, note: str = "x"):
        ran.append(f"{user_id}:{note}")

    # memory driver
    mem = MemoryQueue()
    await mem.dispatch("conf.shared_shape", user_id=1, note="mem")
    await mem.run_pending()

    # saq driver over real redis
    driver = SaqQueue(url=REDIS_URL, name=_namespace())
    try:
        await driver.dispatch("conf.shared_shape", user_id=2, note="saq")
        worker = driver.build_worker(burst=True, dequeue_timeout=0.5)
        await worker.start()

        assert ran == ["1:mem", "2:saq"]
    finally:
        await _purge(driver)


async def test_restart_boundary_job_runs_exactly_once():
    """q1-G2/q2-G3: `queue:restart` with a job mid-flight must end with that
    job COMPLETE, executed exactly once, the sentinel consumed, and the
    replacement worker able to keep processing — no silent loss, no exit
    loop."""
    ran: list[int] = []
    driver = SaqQueue(url=REDIS_URL, name=_namespace())

    @Job(name="it.slow_boundary")
    async def slow_boundary(n: int):
        await asyncio.sleep(0.4)  # in-flight while the sentinel lands
        ran.append(n)

    try:
        await driver.dispatch("it.slow_boundary", n=1)
        await set_restart_sentinel()

        worker = driver.build_worker(burst=True, dequeue_timeout=0.5)
        await worker.start()

        assert ran == [1]  # ran to completion exactly once — never re-queued
        assert await restart_requested_at() is None  # consumed

        # The replacement worker starts clean and processes new work.
        await driver.dispatch("it.slow_boundary", n=2)
        replacement = driver.build_worker(burst=True, dequeue_timeout=0.5)
        await replacement.start()
        assert ran == [1, 2]
    finally:
        await _purge(driver)


async def test_failed_job_is_marked_failed_by_real_worker():
    """A handler exception on the real worker ends FAILED in redis (terminal —
    saq's default retries=1 means one attempt), the after_process failure
    hook's precondition."""
    from saq.job import Status

    driver = SaqQueue(url=REDIS_URL, name=_namespace())

    @Job(name="it.boom")
    async def boom(payload: str):
        raise RuntimeError(f"kaput:{payload}")

    try:
        await driver.dispatch("it.boom", payload="x")
        worker = driver.build_worker(burst=True, dequeue_timeout=0.5)
        await worker.start()

        async for job in driver.queue.iter_jobs(statuses=[Status.FAILED]):
            if job.function == "it.boom":
                assert "kaput:x" in (job.error or "")
                break
        else:
            pytest.fail("expected a FAILED it.boom job in redis")
    finally:
        await _purge(driver)
