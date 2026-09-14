"""Domain events — the Model Event → Domain Event → Queue path (blueprint §8).

Lifecycle handlers stay local to the model; cross-module fire-and-forget work
(a lifecycle hook must not call another module's service) rides a domain
event: ``__dispatches__`` maps lifecycle names to event names, ``dispatch()``
runs in-process listeners and — when a ``@Job`` is registered under the same
name — enqueues the payload for a worker (boundary rule 4).
"""

from __future__ import annotations

import pytest

from fastplace.db import db
from fastplace.orm import Field, Model


@pytest.fixture(autouse=True)
def _isolated():
    from fastplace.events import reset_listeners
    from fastplace.queue import reset_queue, reset_registry

    reset_listeners()
    reset_registry()
    reset_queue()
    yield
    reset_listeners()
    reset_registry()
    reset_queue()


async def test_dispatch_runs_sync_and_async_listeners():
    from fastplace.events import DomainEvent, dispatch, listen

    seen: list[tuple[str, dict]] = []

    def sync_handler(event):  # noqa: ANN001
        seen.append(("sync", dict(event.payload)))

    async def async_handler(event):  # noqa: ANN001
        seen.append(("async", dict(event.payload)))

    listen("order_placed", sync_handler)
    listen("order_placed", async_handler)

    await dispatch(DomainEvent("order_placed", {"order_id": 7}))
    assert seen == [("sync", {"order_id": 7}), ("async", {"order_id": 7})]


async def test_dispatch_auto_enqueues_when_a_job_is_registered():
    from fastplace.events import DomainEvent, dispatch
    from fastplace.queue import Job, MemoryQueue, queue

    calls: list[dict] = []

    @Job()
    async def reindex_document(document_id: int):  # pragma: no cover — test double
        calls.append({"document_id": document_id})

    await dispatch(DomainEvent("reindex_document", {"document_id": 3}))

    memory = queue()
    assert isinstance(memory, MemoryQueue)
    assert len(memory.pending) == 1
    await memory.run_pending()
    assert calls == [{"document_id": 3}]


async def test_dispatch_to_queue_true_requires_the_job():
    from fastplace.events import DomainEvent, dispatch

    with pytest.raises(ValueError, match="unknown job"):
        await dispatch(DomainEvent("never_registered", {}), to_queue=True)


async def test_dispatch_to_queue_false_stays_in_process():
    from fastplace.events import DomainEvent, dispatch
    from fastplace.queue import Job, queue

    @Job()
    async def nosend(message_id: int):  # pragma: no cover — must not run
        raise AssertionError("in-process dispatch must not enqueue")

    await dispatch(DomainEvent("nosend", {"message_id": 1}), to_queue=False)
    assert not getattr(queue(), "pending", [])


async def test_lifecycle_event_dispatches_the_mapped_domain_event(db_url):
    from fastplace.events import DomainEvent, listen

    class Order(Model):
        __tablename__ = "de_orders"
        __dispatches__ = {"created": "order_created", "deleted": "order_deleted"}

        id: int = Field(primary_key=True)
        label: str

    await db.create_all()

    events: list[DomainEvent] = []
    listen("order_created", events.append)
    listen("order_deleted", events.append)

    order = await Order.create(label="first")
    await order.delete()

    assert [e.name for e in events] == ["order_created", "order_deleted"]
    assert events[0].payload == {"model": "Order", "id": order.id}


async def test_dispatch_is_per_model_configuration(db_url):
    from fastplace.events import listen

    class Talk(Model):
        __tablename__ = "de_talks"
        __dispatches__ = {"created": "talk_recorded"}

        id: int = Field(primary_key=True)

    class Memo(Model):
        __tablename__ = "de_memos"

        id: int = Field(primary_key=True)

    await db.create_all()

    fired: list[object] = []
    listen("talk_recorded", fired.append)
    listen("memo_created", fired.append)

    await Memo.create()
    assert fired == []  # no __dispatches__ on Memo → nothing fires


async def test_dispatch_failure_propagates_like_a_lifecycle_handler(db_url):
    """A raising listener aborts the operation — the same contract the
    lifecycle registry already has (handlers are part of the operation)."""

    class Post(Model):
        __tablename__ = "de_posts"
        __dispatches__ = {"created": "post_published"}

        id: int = Field(primary_key=True)
        title: str

    await db.create_all()

    from fastplace.events import listen

    def boom(event):  # noqa: ANN001
        raise RuntimeError("listener failed")

    listen("post_published", boom)

    with pytest.raises(RuntimeError, match="listener failed"):
        await Post.create(title="x")


async def test_domain_event_enqueues_only_after_commit(db_url):
    """The queue leg must wait for the enclosing transaction to commit — a
    worker (or another process) cannot see an uncommitted row, and a rollback
    must not leave an orphaned job behind."""
    from fastplace.queue import Job, MemoryQueue, queue

    @Job()
    async def order_created(model: str, id: int):  # pragma: no cover — test double
        pass

    class Order(Model):
        __tablename__ = "defer_orders"
        __dispatches__ = {"created": "order_created"}

        id: int = Field(primary_key=True)
        name: str

    await db.create_all()

    from fastplace.db import db as _db

    async with _db.transaction():
        order = await Order.create(name="in-flight")
        memory = queue()
        assert isinstance(memory, MemoryQueue)
        # Inside the transaction the enqueue is buffered, not visible.
        assert len(memory.pending) == 0

    # Committed → the buffered event reaches the queue.
    assert len(memory.pending) == 1
    assert memory.pending[0].kwargs == {"model": "Order", "id": order.id}


async def test_rollback_discards_buffered_domain_events(db_url):
    from fastplace.queue import Job, MemoryQueue, queue

    @Job()
    async def order_created(model: str, id: int):  # pragma: no cover — test double
        pass

    class Order(Model):
        __tablename__ = "rb_orders"
        __dispatches__ = {"created": "order_created"}

        id: int = Field(primary_key=True)
        name: str

    await db.create_all()

    from fastplace.db import db as _db

    with pytest.raises(RuntimeError, match="boom"):
        async with _db.transaction():
            await Order.create(name="doomed")
            raise RuntimeError("boom")

    memory = queue()
    assert isinstance(memory, MemoryQueue)
    assert len(memory.pending) == 0
    assert await Order.count() == 0


async def test_enqueue_failure_never_fails_the_committed_write(db_url, caplog):
    """A broker outage after commit must not turn a durable write into a
    client-visible error — the auto-enqueue logs and gives up (the explicit
    ``to_queue=True`` path stays strict)."""
    from fastplace.queue import Job, queue

    @Job()
    async def order_created(model: str, id: int):  # pragma: no cover — test double
        pass

    class Order(Model):
        __tablename__ = "oos_orders"
        __dispatches__ = {"created": "order_created"}

        id: int = Field(primary_key=True)
        name: str

    await db.create_all()

    memory = queue()
    original = memory.dispatch

    async def broken(name, **kwargs):  # noqa: ANN001
        raise ConnectionError("redis unavailable")

    memory.dispatch = broken  # type: ignore[method-assign]
    try:
        await Order.create(name="survives")
    finally:
        memory.dispatch = original  # type: ignore[method-assign]

    assert await Order.count() == 1
    assert any("order_created" in r.message for r in caplog.records if r.levelname >= "ERROR")


def test_dispatches_keys_validate_at_class_definition(db_url):
    """Only post-flush lifecycle events can map to queue work (the payload
    carries the pk) — typos and pre-flush names fail at class definition."""
    with pytest.raises(ValueError, match="creating"):

        class Bad(Model):
            __tablename__ = "bad_orders"
            __dispatches__ = {"creating": "order_creating"}

            id: int = Field(primary_key=True)

    with pytest.raises(ValueError, match="unknown"):

        class Typo(Model):
            __tablename__ = "typo_orders"
            __dispatches__ = {"cretaed": "order_created"}

            id: int = Field(primary_key=True)


async def test_run_pending_drains_only_the_batch_present_at_entry(db_url):
    """A job whose side effect enqueues more work must not loop the drain
    forever — run_pending executes what was queued when it started; the new
    work waits for the next drain."""
    from fastplace.queue import Job, MemoryQueue, queue

    hops: list[int] = []

    @Job()
    async def chain(step: int):
        hops.append(step)
        if step < 3:
            await queue().dispatch("chain", step=step + 1)

    memory = queue()
    assert isinstance(memory, MemoryQueue)
    await memory.dispatch("chain", step=1)

    executed = await memory.run_pending()
    assert executed == 1
    assert hops == [1]
    # The chained hop is pending for the next drain, not lost.
    assert [p.kwargs["step"] for p in memory.pending] == [2]

    await memory.run_pending()
    await memory.run_pending()
    assert hops == [1, 2, 3]
    assert not memory.pending


async def test_dispatch_without_consumer_logs_a_warning(caplog):
    """A domain event nobody consumes (no listener, no job) is almost always
    mis-wiring — surface it instead of silently skipping cross-module work."""
    import logging

    from fastplace.events import DomainEvent, dispatch

    with caplog.at_level(logging.WARNING, logger="fastplace.events"):
        await dispatch(DomainEvent("nobody_listens", {"x": 1}))

    assert any("nobody_listens" in r.message for r in caplog.records)
