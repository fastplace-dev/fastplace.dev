"""Task 15 — FailedJobStore: the persisted ledger behind the queue:failed CLI.

Real sqlite file per test (the DatabaseCache fixture pattern): the store is
Core-only and must stay portable across SQLite/PG/MySQL. The last section
covers the worker wiring — both drivers must land failures in this table.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Every test gets its own sqlite file, db facade, and store singleton."""
    from fastplace.db import reset_db
    from fastplace.queue_failures import reset_failed_job_store

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/failed_jobs.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    reset_failed_job_store()
    yield
    reset_db()
    reset_failed_job_store()


@pytest.fixture(autouse=True)
def _fresh_registry():
    from fastplace.queue import reset_queue, reset_registry

    reset_registry()
    reset_queue()
    yield
    reset_registry()
    reset_queue()


async def _age_row(job_id: int, hours: int) -> None:
    """Rewind one record's failed_at — simulating an old failure."""
    from fastplace.db import db
    from fastplace.queue_failures import _failed_jobs_table, utcnow

    stmt = (
        update(_failed_jobs_table)
        .where(_failed_jobs_table.c.id == job_id)
        .values(failed_at=utcnow() - timedelta(hours=hours))
    )
    async with db.manager.engine("default").begin() as conn:
        await conn.execute(stmt)


# ---------------------------------------------------------------------------
# store CRUD
# ---------------------------------------------------------------------------


async def test_record_returns_id_and_list_roundtrips():
    from fastplace.queue_failures import failed_job_store, utcnow

    job_id = await failed_job_store().record(
        "billing.reconcile", {"invoice_id": 7}, "RuntimeError: kaput"
    )
    assert isinstance(job_id, int) and job_id > 0

    rows = await failed_job_store().list()
    assert len(rows) == 1
    row = rows[0]
    assert row.id == job_id
    assert row.name == "billing.reconcile"
    assert row.kwargs == {"invoice_id": 7}  # the JSON column round-trips the dict
    assert row.error == "RuntimeError: kaput"
    assert abs((utcnow() - row.failed_at).total_seconds()) < 10  # naive UTC, just now


async def test_list_orders_newest_first_and_paginates():
    from fastplace.queue_failures import failed_job_store

    store = failed_job_store()
    for n in range(3):
        await store.record(f"job_{n}", {"n": n}, "err")

    rows = await store.list()
    assert [row.name for row in rows] == ["job_2", "job_1", "job_0"]  # newest first
    page = await store.list(offset=1, limit=1)
    assert [row.name for row in page] == ["job_1"]


async def test_get_returns_the_row_or_none():
    from fastplace.queue_failures import failed_job_store

    store = failed_job_store()
    job_id = await store.record("solo", {"a": 1}, "err")
    row = await store.get(job_id)
    assert row is not None and row.name == "solo"
    assert await store.get(424242) is None


async def test_delete_reports_whether_the_row_existed():
    from fastplace.queue_failures import failed_job_store

    store = failed_job_store()
    job_id = await store.record("solo", {}, "err")
    assert await store.delete(424242) is False  # missing row: False, not an error
    assert await store.delete(job_id) is True
    assert await store.get(job_id) is None


async def test_prune_honors_the_cutoff():
    from fastplace.queue_failures import failed_job_store, utcnow

    store = failed_job_store()
    old_id = await store.record("old_job", {}, "err")
    new_id = await store.record("new_job", {}, "err")
    await _age_row(old_id, hours=3)

    pruned = await store.prune(utcnow() - timedelta(hours=1))
    assert pruned == 1
    remaining = await store.list()
    assert [row.id for row in remaining] == [new_id]  # only the fresh row survives


async def test_flush_counts_and_empties_the_table():
    from fastplace.queue_failures import failed_job_store

    store = failed_job_store()
    await store.record("a", {}, "err")
    await store.record("b", {}, "err")
    assert await store.flush() == 2
    assert await store.list() == []
    assert await store.flush() == 0  # second flush has nothing left


async def test_ensure_table_is_idempotent():
    from fastplace.queue_failures import failed_job_store

    store = failed_job_store()
    await store.ensure_table()
    await store.ensure_table()  # second call is a no-op, not an error
    assert await store.list() == []


def test_failed_job_store_is_a_singleton_until_reset():
    from fastplace.queue_failures import failed_job_store, reset_failed_job_store

    first = failed_job_store()
    assert failed_job_store() is first
    reset_failed_job_store()
    assert failed_job_store() is not first


# ---------------------------------------------------------------------------
# job_key — the aborted-scan dedupe column (q2-G2)
# ---------------------------------------------------------------------------


async def test_record_persists_the_job_key():
    from fastplace.queue_failures import failed_job_store

    job_id = await failed_job_store().record(
        "billing.reconcile", {"invoice_id": 1}, "aborted: swept", job_key="abc"
    )
    row = await failed_job_store().get(job_id)
    assert row.job_key == "abc"

    plain_id = await failed_job_store().record("no_key", {}, "err")  # pre-column shape
    assert (await failed_job_store().get(plain_id)).job_key is None


async def test_record_once_dedupes_on_the_job_key():
    """The crash-loss scan runs every 60s — without a job_key guard the same
    ABORTED saq job would be re-recorded on every pass until its redis TTL
    expires. record_once skips when the key already has a row."""
    from fastplace.queue_failures import failed_job_store

    store = failed_job_store()
    first = await store.record_once("export.csv", {"batch": 1}, "aborted: swept", job_key="k1")
    assert first is not None  # recorded

    again = await store.record_once("export.csv", {"batch": 1}, "aborted: swept", job_key="k1")
    assert again is None  # duplicate pass: skipped

    rows = await store.list()
    assert len(rows) == 1  # exactly one row for the aborted job

    other = await store.record_once("export.csv", {"batch": 2}, "aborted: swept", job_key="k2")
    assert other is not None  # a different job key is a different failure


async def test_ensure_table_upgrades_a_legacy_table_without_job_key():
    """Deployments that created the table before job_key existed must be
    upgraded in place — ALTER under the same idempotent ensure_table()."""
    from sqlalchemy import text

    from fastplace.db import db
    from fastplace.queue_failures import failed_job_store

    # Hand-build the legacy shape (pre-job_key) exactly as it shipped.
    async with db.manager.engine("default").begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE _fastplace_failed_jobs ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL, kwargs JSON NOT NULL, "
                "error TEXT NOT NULL, failed_at DATETIME NOT NULL)"
            )
        )

    store = failed_job_store()
    await store.ensure_table()  # must ALTER, not crash on the existing table
    job_id = await store.record("legacy.job", {}, "aborted: swept", job_key="legacy-1")
    row = await store.get(job_id)
    assert row.job_key == "legacy-1"
    # And the dedupe path works over the upgraded column.
    assert await store.record_once("legacy.job", {}, "aborted: swept", job_key="legacy-1") is None


def test_id_column_uses_sqlite_autoincrement():
    """q1-G9: without AUTOINCREMENT, sqlite may reuse a deleted top rowid —
    a retried-and-forgotten failure could resurface under a stale #id."""
    from sqlalchemy import text

    from fastplace.db import db
    from fastplace.queue_failures import failed_job_store

    async def _ddl() -> str:
        await failed_job_store().ensure_table()
        async with db.manager.engine("default").connect() as conn:
            result = await conn.execute(
                text("SELECT sql FROM sqlite_master WHERE name = '_fastplace_failed_jobs'")
            )
            return str(result.scalar_one())

    import asyncio

    ddl = asyncio.run(_ddl())
    assert "AUTOINCREMENT" in ddl.upper()


# ---------------------------------------------------------------------------
# worker wiring — failures must land in the store from both drivers
# ---------------------------------------------------------------------------


async def test_memory_drain_records_failures_to_the_store():
    from fastplace.queue import Job, MemoryQueue
    from fastplace.queue_failures import failed_job_store

    @Job(name="t15_boom")
    async def boom(user_id: int) -> None:
        raise RuntimeError("kaput")

    mem = MemoryQueue()
    await mem.dispatch("t15_boom", user_id=7)
    await mem.run_pending()

    rows = await failed_job_store().list()
    assert len(rows) == 1
    assert rows[0].name == "t15_boom"
    assert rows[0].kwargs == {"user_id": 7}
    assert "RuntimeError" in rows[0].error and "kaput" in rows[0].error


async def test_memory_drain_survives_a_broken_failure_store(monkeypatch):
    """Persistence is best-effort: a store that cannot write must never take
    the drain (or its in-process failure ledger) down with it."""
    import fastplace.queue_failures as queue_failures
    from fastplace.queue import Job, MemoryQueue

    class _BrokenStore:
        async def record(self, *args, **kwargs):
            raise RuntimeError("db down")

    monkeypatch.setattr(queue_failures, "failed_job_store", lambda: _BrokenStore())

    @Job(name="t15_broken_store_boom")
    async def boom() -> None:
        raise ValueError("nope")

    mem = MemoryQueue()
    await mem.dispatch("t15_broken_store_boom")
    executed = await mem.run_pending()
    assert executed == 1
    assert len(mem.failures) == 1  # the in-process ledger still captured it


async def test_saq_after_process_hook_records_terminal_failures():
    from saq import Status

    from fastplace.queue import _record_saq_failure
    from fastplace.queue_failures import failed_job_store

    class _SaqJob:
        function = "billing.reconcile"
        kwargs = {"invoice_id": 3}
        status = Status.FAILED

    await _record_saq_failure({"job": _SaqJob(), "exception": RuntimeError("kaput")})

    rows = await failed_job_store().list()
    assert len(rows) == 1
    assert rows[0].name == "billing.reconcile"
    assert rows[0].kwargs == {"invoice_id": 3}
    assert "RuntimeError" in rows[0].error


async def test_saq_hook_skips_successful_and_retryable_jobs():
    from saq import Status

    from fastplace.queue import _record_saq_failure
    from fastplace.queue_failures import failed_job_store

    class _SaqJob:
        function = "j"
        kwargs = {}
        status = Status.QUEUED  # retryable failure: saq re-queued it, not terminal

    await _record_saq_failure({"job": _SaqJob(), "exception": RuntimeError("will retry")})
    await _record_saq_failure({"job": _SaqJob()})  # success: no exception in ctx

    assert await failed_job_store().list() == []
