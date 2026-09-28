"""q1-G6 — batch fan-out: ``dispatch_many``.

One call enqueues a whole batch of plain dispatches and returns the handles
in input order. The batch is validated atomically: an unknown job name
anywhere in the list raises BEFORE anything is enqueued, so a bad batch can
never half-apply (looping ``dispatch()`` by hand has no such guarantee —
item 3 of 5 failing leaves items 1-2 already queued).
"""

from __future__ import annotations

import pytest

from fastplace.queue import (
    Job,
    MemoryQueue,
    SaqQueue,
    reset_queue,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _fresh_queue():
    reset_registry()
    reset_queue()
    yield
    reset_registry()
    reset_queue()


class _BatchSaqQueue:
    """Fake saq queue: enqueue records the Job object and returns it."""

    def __init__(self) -> None:
        self.recorded: list[object] = []

    async def enqueue(self, job):
        self.recorded.append(job)
        return job


# ---------------------------------------------------------------------------
# memory driver — batch drains in order, handles track each dispatch
# ---------------------------------------------------------------------------


async def test_dispatch_many_runs_every_job_in_input_order():
    ran: list[int] = []

    @Job(name="fan.notify")
    async def notify(user_id: int) -> None:
        ran.append(user_id)

    q = MemoryQueue()
    handles = await q.dispatch_many(
        [
            ("fan.notify", {"user_id": 1}),
            ("fan.notify", {"user_id": 2}),
            ("fan.notify", {"user_id": 3}),
        ]
    )

    assert await q.run_pending() == 3
    assert ran == [1, 2, 3]  # batch order is execution order
    assert len(handles) == 3
    assert [await q.job_status(h.key) for h in handles] == ["completed"] * 3


async def test_dispatch_many_validates_atomically_nothing_enqueued_on_a_bad_batch():
    @Job(name="fan.ok")
    async def ok() -> None:
        return None

    q = MemoryQueue()
    batch = [
        ("fan.ok", {}),
        ("fan.does_not_exist", {}),  # unknown name mid-batch
        ("fan.ok", {}),
    ]

    with pytest.raises(ValueError, match="fan.does_not_exist"):
        await q.dispatch_many(batch)

    # The whole batch was refused — not two jobs queued and an error raised
    # over them (the hand-rolled loop's failure mode this API exists to fix).
    assert await q.queue_depth() == 0
    assert list(q.pending) == []


async def test_dispatch_many_empty_batch_is_empty():
    @Job(name="fan.ok")
    async def ok() -> None:
        return None

    q = MemoryQueue()
    assert await q.dispatch_many([]) == []
    assert await q.queue_depth() == 0


# ---------------------------------------------------------------------------
# saq driver — one enqueued saq Job per item, envelope carried
# ---------------------------------------------------------------------------


async def test_dispatch_many_enqueues_each_saq_job_with_the_envelope():
    @Job(name="fan.mail")
    async def mail(user_id: int) -> None:
        return None

    driver = SaqQueue(queue=_BatchSaqQueue())
    handles = await driver.dispatch_many(
        [("fan.mail", {"user_id": 7}), ("fan.mail", {"user_id": 8})]
    )

    recorded = driver.queue.recorded
    assert len(recorded) == 2
    assert [job.function for job in recorded] == ["fan.mail", "fan.mail"]
    assert [job.kwargs for job in recorded] == [{"user_id": 7}, {"user_id": 8}]
    # The standard resolved envelope rides every item (framework defaults
    # here: retries=3, timeout=60 — same as a single dispatch).
    assert all(job.retries == 3 for job in recorded)
    assert all(job.timeout == 60.0 for job in recorded)
    # Handles are the enqueued Jobs, in input order.
    assert handles == recorded


async def test_dispatch_many_saq_validates_atomically_too():
    @Job(name="fan.ok")
    async def ok() -> None:
        return None

    driver = SaqQueue(queue=_BatchSaqQueue())
    with pytest.raises(ValueError, match="fan.does_not_exist"):
        await driver.dispatch_many([("fan.ok", {}), ("fan.does_not_exist", {})])
    assert driver.queue.recorded == []  # nothing reached the broker
