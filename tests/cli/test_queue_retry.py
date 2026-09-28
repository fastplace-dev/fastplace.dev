"""Task 16 CLI — `queue:retry <ID>|all` (spec #35).

Retry re-dispatches from the persisted FailedJobStore through
``queue().dispatch(name, **kwargs)``: a successful dispatch deletes the
record, a dispatch error keeps it (fix and retry again) and exits 1.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from tests.cli._isolation import isolate_project_state  # noqa: F401

# Rich colorizes output when the environment forces color; strip codes so
# assertions match on plain text.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


class _FakeQueue:
    """Stands in for queue() — captures dispatch instead of enqueuing."""

    def __init__(self, fail_for: set[str] | None = None):
        self.dispatched: list[tuple[str, dict]] = []
        self._fail_for = fail_for or set()

    async def dispatch(self, name, **kwargs):
        if name in self._fail_for:
            raise RuntimeError(f"broker down for {name}")
        self.dispatched.append((name, kwargs))


@pytest.fixture(autouse=True)
def _fresh_queue_state():
    """Registry, queue singleton, and store singleton never leak between tests."""
    from fastplace.queue import reset_queue, reset_registry
    from fastplace.queue_failures import reset_failed_job_store

    reset_registry()
    reset_queue()
    reset_failed_job_store()
    yield
    reset_registry()
    reset_queue()
    reset_failed_job_store()


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/retry.db")
    monkeypatch.delenv("QUEUE_DRIVER", raising=False)  # memory driver by default
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _record(project, name, kwargs=None, error="RuntimeError: kaput") -> int:
    from fastplace.queue_failures import failed_job_store

    return asyncio.run(failed_job_store().record(name, kwargs or {}, error))


def _get(project, job_id):
    from fastplace.queue_failures import failed_job_store

    return asyncio.run(failed_job_store().get(job_id))


# ---------------------------------------------------------------------------
# queue:retry <ID>
# ---------------------------------------------------------------------------


def test_retry_redispatches_with_original_kwargs_and_deletes_record(project, monkeypatch):
    from fastplace.queue import Job

    @Job(name="t16_retry_job")
    async def dummy(user_id: int) -> None:
        return None

    job_id = _record(project, "t16_retry_job", {"user_id": 9}, "RuntimeError: first try")
    fake = _FakeQueue()
    monkeypatch.setattr("fastplace.queue.queue", lambda: fake)

    result = runner.invoke(cli_app, ["queue:retry", str(job_id)])
    assert result.exit_code == 0, result.output
    assert fake.dispatched == [("t16_retry_job", {"user_id": 9})]
    assert _get(project, job_id) is None  # the record died with the re-dispatch


def test_retry_unknown_id_exits_one(project):
    result = runner.invoke(cli_app, ["queue:retry", "424242"])
    assert result.exit_code == 1
    assert "424242" in result.output


def test_retry_non_numeric_target_exits_one(project):
    result = runner.invoke(cli_app, ["queue:retry", "banana"])
    assert result.exit_code == 1
    assert "banana" in result.output


def test_retry_dispatch_error_keeps_record_and_exits_one(project, monkeypatch):
    job_id = _record(project, "t16_stuck", {"n": 1}, "RuntimeError: first try")
    fake = _FakeQueue(fail_for={"t16_stuck"})
    monkeypatch.setattr("fastplace.queue.queue", lambda: fake)

    result = runner.invoke(cli_app, ["queue:retry", str(job_id)])
    assert result.exit_code == 1
    plain = ANSI_RE.sub("", result.output)
    assert "broker down" in plain  # the dispatch error is surfaced, not swallowed
    assert _get(project, job_id) is not None  # kept for the next fix-and-retry


def test_retry_unregistered_job_keeps_record(project):
    """The real memory driver validates handler names: a job whose module was
    removed cannot be retried, and its record must survive that discovery."""
    job_id = _record(project, "t16_not_registered_anywhere", {})

    result = runner.invoke(cli_app, ["queue:retry", str(job_id)])
    assert result.exit_code == 1
    assert _get(project, job_id) is not None


# ---------------------------------------------------------------------------
# queue:retry all
# ---------------------------------------------------------------------------


def test_retry_all_drains_the_store(project, monkeypatch):
    _record(project, "t16_a", {"n": 1})
    _record(project, "t16_b", {"n": 2})
    fake = _FakeQueue()
    monkeypatch.setattr("fastplace.queue.queue", lambda: fake)

    result = runner.invoke(cli_app, ["queue:retry", "all"])
    assert result.exit_code == 0, result.output
    assert sorted(fake.dispatched) == [("t16_a", {"n": 1}), ("t16_b", {"n": 2})]
    from fastplace.queue_failures import failed_job_store

    assert asyncio.run(failed_job_store().list()) == []


def test_retry_without_argument_defaults_to_all(project, monkeypatch):
    _record(project, "t16_default", {"n": 1})
    fake = _FakeQueue()
    monkeypatch.setattr("fastplace.queue.queue", lambda: fake)

    result = runner.invoke(cli_app, ["queue:retry"])
    assert result.exit_code == 0, result.output
    assert fake.dispatched == [("t16_default", {"n": 1})]


def test_retry_all_on_empty_store_exits_zero(project):
    result = runner.invoke(cli_app, ["queue:retry", "all"])
    assert result.exit_code == 0, result.output
    assert "no failed jobs" in ANSI_RE.sub("", result.output)


def test_retry_all_keeps_records_whose_dispatch_failed(project, monkeypatch):
    _record(project, "t16_ok", {"n": 1})
    stuck_id = _record(project, "t16_bad", {"n": 2})
    fake = _FakeQueue(fail_for={"t16_bad"})
    monkeypatch.setattr("fastplace.queue.queue", lambda: fake)

    result = runner.invoke(cli_app, ["queue:retry", "all"])
    assert result.exit_code == 1
    assert fake.dispatched == [("t16_ok", {"n": 1})]  # the healthy one still went out
    assert _get(project, stuck_id) is not None  # the stuck one stayed on the ledger


def test_retry_error_line_renders_markup_literally(project, monkeypatch):
    """A failed-job name and its dispatch error are data, not Rich markup —
    both must render literally on the retry error line (final review)."""

    class _MarkupFailingQueue:
        async def dispatch(self, name, **kwargs):
            raise RuntimeError("broker [bold]down[/]")

    job_id = _record(project, "t16-[bold]stuck[/]", {"n": 1})
    monkeypatch.setattr("fastplace.queue.queue", lambda: _MarkupFailingQueue())

    result = runner.invoke(cli_app, ["queue:retry", str(job_id)])
    assert result.exit_code == 1
    plain = ANSI_RE.sub("", result.output)
    assert "t16-[bold]stuck[/]" in plain
    assert "broker [bold]down[/]" in plain
