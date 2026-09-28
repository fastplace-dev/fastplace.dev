"""q2-G2 — crash-loss visibility: the aborted-job scan.

When a saq worker dies mid-job, saq's sweeper aborts the job (status
ABORTED, error ``swept``) inside redis — where nothing human browses. The
scan enumerates ABORTED jobs and records each to the failed-job ledger
exactly once (dedupe on the saq job key), so ``queue:failed`` /
``queue:health`` surface crash-loss instead of silently dropping it.
"""

from __future__ import annotations

import pytest
from saq.job import Status


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Own sqlite file + store singleton per test (the failures-file pattern)."""
    from fastplace.db import reset_db
    from fastplace.queue_failures import reset_failed_job_store

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/aborted.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    reset_failed_job_store()
    yield
    reset_db()
    reset_failed_job_store()


@pytest.fixture(autouse=True)
def _fresh_queue():
    from fastplace.queue import reset_queue, reset_registry

    reset_registry()
    reset_queue()
    yield
    reset_registry()
    reset_queue()


class _AbortedJob:
    """The saq Job surface the scan reads (key/function/kwargs/error/status)."""

    def __init__(self, key: str, function: str, kwargs: dict, error: str) -> None:
        self.key = key
        self.function = function
        self.kwargs = kwargs
        self.error = error
        self.status = Status.ABORTED


class _FakeSaqQueue:
    """Queue whose redis SCAN surface is a scriptable list of jobs."""

    def __init__(self, jobs: list) -> None:
        self._jobs = jobs
        self.statuses_seen: list[list] = []

    async def iter_jobs(self, statuses=None, batch_size: int = 100):
        self.statuses_seen.append(list(statuses or []))
        for job in self._jobs:
            yield job


@pytest.fixture
def driver():
    from fastplace.queue import SaqQueue

    return SaqQueue(queue=_FakeSaqQueue([]))


async def test_aborted_jobs_enumerates_the_aborted_status(driver):
    driver.queue._jobs = [
        _AbortedJob("k1", "export.csv", {"batch": 1}, "swept"),
        _AbortedJob("k2", "export.csv", {"batch": 2}, "cancelled by admin"),
    ]
    jobs = await driver.aborted_jobs()
    assert [job.key for job in jobs] == ["k1", "k2"]
    # The scan filters for the terminal ABORTED status — a crashed worker's
    # jobs land there via the sweeper; nothing else is crash-loss.
    from saq.job import Status as S

    assert driver.queue.statuses_seen == [[S.ABORTED]]


async def test_record_aborted_jobs_lands_each_in_the_ledger(driver):
    from fastplace.queue_failures import failed_job_store

    driver.queue._jobs = [_AbortedJob("k1", "export.csv", {"batch": 7}, "swept")]
    recorded = await driver.record_aborted_jobs()

    assert recorded == ["export.csv"]
    rows = await failed_job_store().list()
    assert len(rows) == 1
    assert rows[0].name == "export.csv"
    assert rows[0].kwargs == {"batch": 7}
    assert "swept" in rows[0].error  # the saq error text tells the story
    assert rows[0].job_key == "k1"


async def test_record_aborted_jobs_dedupes_across_scan_passes(driver):
    """The companion scan runs every 60s against redis where the ABORTED job
    sits until TTL — pass 2 must not re-record what pass 1 already kept."""
    from fastplace.queue_failures import failed_job_store

    driver.queue._jobs = [_AbortedJob("k1", "export.csv", {"batch": 7}, "swept")]
    assert await driver.record_aborted_jobs() == ["export.csv"]
    assert await driver.record_aborted_jobs() == []  # same job, deduped

    rows = await failed_job_store().list()
    assert len(rows) == 1


async def test_record_aborted_jobs_survives_a_broken_store(driver, monkeypatch):
    """The scan is a companion, never a load-bearing wall: a store outage
    logs and the worker keeps working (the record_failure precedent)."""
    import fastplace.queue_failures as queue_failures

    driver.queue._jobs = [_AbortedJob("k1", "export.csv", {}, "swept")]

    class _BrokenStore:
        async def record_once(self, *args, **kwargs):
            raise RuntimeError("db down")

    monkeypatch.setattr(queue_failures, "failed_job_store", lambda: _BrokenStore())
    # Must not raise — the companion task would otherwise kill the worker.
    assert await driver.record_aborted_jobs() == []
