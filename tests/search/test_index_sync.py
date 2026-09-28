"""Index-sync pipeline — enrollment, soft-delete flow, the queue leg.

``make_searchable`` wires model lifecycle events to the framework-owned
``search_index_sync`` job: the record is serialized at event time, the queue
leg waits for the enclosing commit (a rollback discards it), and the worker
applies the op to the active engine. ``queue=False`` keeps the engine call
in-process for dev/tests. The engine here is a fake — a real engine is any
object with the four async methods (see the docs example).
"""

from __future__ import annotations

import logging

import pytest


class FakeEngine:
    """Records every call — the whole engine surface, nothing hidden."""

    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.deletes: list[dict] = []
        self.flushes: list[type] = []

    async def update(self, records: list) -> int:
        self.updates.extend(dict(r) for r in records)
        return len(records)

    async def delete(self, records: list) -> int:
        self.deletes.extend(dict(r) for r in records)
        return len(records)

    async def flush(self, model: type) -> None:
        self.flushes.append(model)

    async def search(self, query: str, *, model: type | None = None, limit: int = 20) -> list:
        return []


@pytest.fixture(autouse=True)
def _sync_isolation():
    """Registry isolation + model-table sweep per test (same rules as ORM)."""
    from fastplace.db import reset_db
    from fastplace.events import reset_listeners
    from fastplace.queue import reset_queue
    from fastplace.search import (
        ensure_index_sync_job,
        reset_engine,
        reset_searchable,
    )
    from tests._registry import dispose_all_models, metadata_baseline, sweep_added_tables

    reset_db()
    dispose_all_models()
    baseline = metadata_baseline()
    reset_listeners()
    reset_queue()
    reset_engine()
    reset_searchable()
    ensure_index_sync_job()  # reset_registry above wiped the framework job
    yield
    dispose_all_models()
    sweep_added_tables(baseline)


@pytest.fixture()
def db_url(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    return "sqlite+aiosqlite:///:memory:"


@pytest.fixture()
def post_model(db_url):
    from fastplace.db import db
    from fastplace.orm import Field, Model

    class Post(Model):
        __tablename__ = "sync_posts"
        __searchable__ = ["title"]

        id: int = Field(primary_key=True)
        title: str

    return db, Post


# ---------------------------------------------------------------------------
# enrollment
# ---------------------------------------------------------------------------


def test_make_searchable_requires_a_searchable_declaration():
    from fastplace.orm import Field, Model
    from fastplace.search import NotSearchable, make_searchable

    class Plain(Model):
        __tablename__ = "sync_plain"

        id: int = Field(primary_key=True)

    with pytest.raises(NotSearchable, match="__searchable__"):
        make_searchable(Plain)


def test_make_searchable_enrolls_and_unregister_detaches(post_model):
    from fastplace.queue import jobs
    from fastplace.search import (
        SEARCH_INDEX_SYNC_JOB,
        make_searchable,
        searchable_models,
        unregister_searchable,
    )

    _, Post = post_model
    make_searchable(Post, engine=FakeEngine(), queue=False)

    assert len(searchable_models()) == 1
    assert SEARCH_INDEX_SYNC_JOB in jobs()

    unregister_searchable(Post)
    assert searchable_models() == []


def test_unregister_is_idempotent(post_model):
    from fastplace.search import make_searchable, unregister_searchable

    _, Post = post_model
    make_searchable(Post, engine=FakeEngine(), queue=False)
    unregister_searchable(Post)
    unregister_searchable(Post)  # no raise


def test_re_enrollment_replaces_rather_than_doubles(post_model):
    from fastplace.search import make_searchable, searchable_models

    _, Post = post_model
    make_searchable(Post, engine=FakeEngine(), queue=False)
    make_searchable(Post, engine=FakeEngine(), queue=False)
    assert len(searchable_models()) == 1


def test_enrollment_rearms_a_wiped_job_registry(post_model):
    """reset_registry() (any suite's isolation) must not silently kill the
    pipeline — enrollment and the dispatch leg re-arm the framework job."""
    from fastplace.queue import jobs, reset_registry
    from fastplace.search import SEARCH_INDEX_SYNC_JOB, make_searchable

    _, Post = post_model
    make_searchable(Post, engine=FakeEngine(), queue=False)
    reset_registry()
    assert SEARCH_INDEX_SYNC_JOB not in jobs()

    from fastplace.search import ensure_index_sync_job

    ensure_index_sync_job()
    assert SEARCH_INDEX_SYNC_JOB in jobs()


# ---------------------------------------------------------------------------
# in-process sync (queue=False) against a fake engine
# ---------------------------------------------------------------------------


async def test_lifecycle_keeps_a_fake_engine_current(post_model):
    from fastplace.search import make_searchable

    db, Post = post_model
    engine = FakeEngine()
    make_searchable(Post, engine=engine, queue=False)
    await db.create_all()

    post = await Post.create(title="first")
    assert engine.updates == [{"id": post.id, "title": "first"}]

    await post.update(title="second")
    assert engine.updates[-1] == {"id": post.id, "title": "second"}

    await post.delete()  # soft tombstone — leaves the index
    assert engine.deletes == [{"id": post.id, "title": "second"}]

    await post.restore()  # back into the index
    assert engine.updates[-1] == {"id": post.id, "title": "second"}

    await post.force_delete()  # hard removal — leaves too
    assert engine.deletes[-1] == {"id": post.id, "title": "second"}


async def test_in_process_engine_failure_fails_loudly(post_model):
    """queue=False is the dev/tests mode: a broken engine is a broken write,
    the same contract lifecycle handlers already have."""

    class Boom(FakeEngine):
        async def update(self, records: list) -> int:
            raise ConnectionError("engine down")

    from fastplace.search import make_searchable

    db, Post = post_model
    make_searchable(Post, engine=Boom(), queue=False)
    await db.create_all()

    with pytest.raises(ConnectionError, match="engine down"):
        await Post.create(title="x")


async def test_instances_of_other_classes_do_not_index_under_the_enrollment(post_model):
    """The handler filters on exact class — a stray instance (a subclass
    firing an inherited registry, a reused handler) never lands in the
    parent's index."""
    from types import SimpleNamespace

    from fastplace.search import make_searchable

    _, Post = post_model
    engine = FakeEngine()
    make_searchable(Post, engine=engine, queue=False)

    handler = Post._fastplace_events._handlers["created"][0]  # noqa: SLF001
    strays = SimpleNamespace(id=1, title="not a Post")
    await handler(strays)
    assert engine.updates == []


# ---------------------------------------------------------------------------
# the queue leg
# ---------------------------------------------------------------------------


async def test_enqueue_waits_for_the_enclosing_commit(post_model):
    from fastplace.queue import MemoryQueue, queue
    from fastplace.search import make_searchable

    db, Post = post_model
    make_searchable(Post, engine=None, queue=True)
    await db.create_all()

    async with db.transaction():
        post = await Post.create(title="in-flight")
        memory = queue()
        assert isinstance(memory, MemoryQueue)
        assert len(memory.pending) == 0  # buffered — a worker can't see it yet

    memory = queue()
    assert len(memory.pending) == 1
    kwargs = memory.pending[0].kwargs
    assert kwargs["op"] == "update"
    assert kwargs["record"] == {"id": post.id, "title": "in-flight"}
    assert kwargs["model"].endswith("Post")


async def test_rollback_discards_the_buffered_sync(post_model):
    from fastplace.queue import MemoryQueue, queue
    from fastplace.search import make_searchable

    db, Post = post_model
    make_searchable(Post, queue=True)
    await db.create_all()

    with pytest.raises(RuntimeError, match="boom"):
        async with db.transaction():
            await Post.create(title="doomed")
            raise RuntimeError("boom")

    memory = queue()
    assert isinstance(memory, MemoryQueue)
    assert len(memory.pending) == 0


async def test_job_applies_records_to_the_active_engine(post_model):
    """The queue leg end-to-end on the memory driver: dispatch → drain → the
    active registry engine sees the record."""
    from fastplace.queue import MemoryQueue, queue
    from fastplace.search import (
        make_searchable,
        register_engine,
    )

    db, Post = post_model
    engine = FakeEngine()
    register_engine(engine)  # type: ignore[arg-type]
    make_searchable(Post, queue=True)
    await db.create_all()

    post = await Post.create(title="queued")
    memory = queue()
    assert isinstance(memory, MemoryQueue)
    assert len(memory.pending) == 1
    await memory.run_pending()

    assert engine.updates == [{"id": post.id, "title": "queued"}]

    await post.delete()
    await memory.run_pending()
    assert engine.deletes == [{"id": post.id, "title": "queued"}]


async def test_dispatch_failure_never_fails_the_committed_write(post_model, caplog):
    from fastplace.queue import queue
    from fastplace.search import SEARCH_INDEX_SYNC_JOB, make_searchable

    db, Post = post_model
    make_searchable(Post, queue=True)
    await db.create_all()

    memory = queue()
    original = memory.dispatch

    async def broken(name, **kwargs):  # noqa: ANN001
        raise ConnectionError("redis unavailable")

    memory.dispatch = broken  # type: ignore[method-assign]
    try:
        post = await Post.create(title="survives")
    finally:
        memory.dispatch = original  # type: ignore[method-assign]

    assert await Post.count() == 1
    assert post.title == "survives"
    assert any(SEARCH_INDEX_SYNC_JOB in r.message for r in caplog.records if r.levelname == "ERROR")


# ---------------------------------------------------------------------------
# the framework job itself
# ---------------------------------------------------------------------------


async def test_job_resolves_a_pinned_engine_over_the_registry(post_model):
    from fastplace.search import (
        make_searchable,
        register_engine,
        search_index_sync,
        unregister_searchable,
    )

    _, Post = post_model
    pinned, registry_engine = FakeEngine(), FakeEngine()
    register_engine(registry_engine)  # type: ignore[arg-type]
    make_searchable(Post, engine=pinned, queue=False)

    await search_index_sync(
        f"{Post.__module__}.{Post.__qualname__}", "update", {"id": 1, "title": "x"}
    )
    assert pinned.updates == [{"id": 1, "title": "x"}]
    assert registry_engine.updates == []
    unregister_searchable(Post)


async def test_job_skips_an_unenrolled_model_without_raising(caplog):
    from fastplace.search import reset_searchable, search_index_sync

    reset_searchable()
    with caplog.at_level(logging.WARNING, logger="fastplace.search"):
        await search_index_sync("nowhere.Gone", "update", {"id": 1})

    assert any("nowhere.Gone" in r.message for r in caplog.records)


async def test_job_rejects_an_unknown_op(post_model):
    from fastplace.search import make_searchable, search_index_sync

    _, Post = post_model
    make_searchable(Post, engine=FakeEngine(), queue=False)

    with pytest.raises(ValueError, match="unknown search index op"):
        await search_index_sync(f"{Post.__module__}.{Post.__qualname__}", "upsert", {"id": 1})
