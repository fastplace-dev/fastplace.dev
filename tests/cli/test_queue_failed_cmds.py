"""Task 15 CLI — `queue:failed`, `queue:flush`, `queue:forget`, `queue:prune-failed`.

The commands read/manage the persisted FailedJobStore; tests record real rows
through the store in a scaffolded tmp project and assert on CLI output and
surviving rows. Only `queue:flush` is destructive, so only it carries the
production confirmation guard (the db:wipe convention).
"""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta

import pytest

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from _isolation import isolate_project_state  # noqa: F401
from sqlalchemy import update
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Rich colorizes output when the environment forces color; strip codes so
# assertions match on plain text.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


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
    """A tmp project whose failed-job table lives in its own sqlite file."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/failed.db")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _record(project, name, kwargs=None, error="RuntimeError: kaput") -> int:
    from fastplace.queue_failures import failed_job_store

    return asyncio.run(failed_job_store().record(name, kwargs or {}, error))


def _rows(project):
    from fastplace.queue_failures import failed_job_store

    return asyncio.run(failed_job_store().list())


# ---------------------------------------------------------------------------
# queue:failed
# ---------------------------------------------------------------------------


def test_queue_failed_empty_state_exits_zero(project):
    result = runner.invoke(cli_app, ["queue:failed"])
    assert result.exit_code == 0, result.output
    assert "no failed jobs" in ANSI_RE.sub("", result.output)


def test_queue_failed_lists_recorded_rows(project):
    _record(project, "billing.reconcile", {"invoice_id": 7})
    _record(project, "mail.welcome", {"user_id": 9})

    result = runner.invoke(cli_app, ["queue:failed"])
    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "billing.reconcile" in plain
    assert "mail.welcome" in plain
    # The error column wraps to the terminal width, so the two halves of the
    # message are asserted separately rather than as one contiguous string.
    assert "RuntimeError:" in plain and "kaput" in plain
    assert "invoice_id" in plain  # the dispatch payload is visible for retry decisions


@pytest.mark.parametrize(
    "option", ["--limit", "--offset"], ids=["negative-limit", "negative-offset"]
)
def test_queue_failed_rejects_negative_paging_options(project, option):
    """A negative --limit/--offset must be a usage error (exit 2) before any
    store query runs — SQLite reads LIMIT -1 as "no limit", so silently
    accepting it would dump the whole ledger (final review)."""
    _record(project, "billing.reconcile")

    result = runner.invoke(cli_app, ["queue:failed", option, "-1"])
    assert result.exit_code == 2
    plain = ANSI_RE.sub("", result.output)
    assert "billing.reconcile" not in plain  # nothing was listed
    assert "no failed jobs" not in plain  # the command never reached the store


# ---------------------------------------------------------------------------
# queue:forget
# ---------------------------------------------------------------------------


def test_queue_forget_deletes_one_record(project):
    job_id = _record(project, "billing.reconcile")

    result = runner.invoke(cli_app, ["queue:forget", str(job_id)])
    assert result.exit_code == 0, result.output
    assert _rows(project) == []


def test_queue_forget_unknown_id_exits_one(project):
    result = runner.invoke(cli_app, ["queue:forget", "424242"])
    assert result.exit_code == 1
    assert "424242" in result.output


# ---------------------------------------------------------------------------
# queue:flush — destructive, so it alone carries the production guard
# ---------------------------------------------------------------------------


def test_queue_flush_force_clears_all_records(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    _record(project, "a")
    _record(project, "b")

    result = runner.invoke(cli_app, ["queue:flush", "--force"])
    assert result.exit_code == 0, result.output
    assert _rows(project) == []
    assert "2" in ANSI_RE.sub("", result.output)


def test_queue_flush_refuses_in_production_without_force(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    _record(project, "a")

    result = runner.invoke(cli_app, ["queue:flush"], input="n\n")
    assert result.exit_code == 1
    assert len(_rows(project)) == 1  # untouched


def test_queue_flush_production_force_skips_the_prompt(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    _record(project, "a")

    result = runner.invoke(cli_app, ["queue:flush", "--force"])
    assert result.exit_code == 0, result.output
    assert "Delete every failed-job record" not in result.output  # no prompt
    assert _rows(project) == []


# ---------------------------------------------------------------------------
# queue:prune-failed
# ---------------------------------------------------------------------------


def test_queue_prune_failed_removes_only_old_rows(project):
    from fastplace.db import db
    from fastplace.queue_failures import _failed_jobs_table, utcnow

    old_id = _record(project, "old_job")
    _record(project, "new_job")

    async def _age():
        stmt = (
            update(_failed_jobs_table)
            .where(_failed_jobs_table.c.id == old_id)
            .values(failed_at=utcnow() - timedelta(hours=3))
        )
        async with db.manager.engine("default").begin() as conn:
            await conn.execute(stmt)

    asyncio.run(_age())

    result = runner.invoke(cli_app, ["queue:prune-failed", "--hours=1"])
    assert result.exit_code == 0, result.output
    rows = _rows(project)
    assert [row.name for row in rows] == ["new_job"]
    assert "1" in ANSI_RE.sub("", result.output)


def test_queue_failed_table_renders_markup_rows_literally(project):
    """Failed-job names/errors are persisted data, not Rich markup — bracket
    tags must survive to the terminal literally (final review), never style
    the table cells."""
    _record(project, "billing.[bold]reconcile[/]", error="RuntimeError: [red]kaput[/]")

    result = runner.invoke(cli_app, ["queue:failed"])
    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "billing.[bold]reconcile[/]" in plain
    assert "[red]kaput[/]" in plain
