"""queue:jobs — list jobs on the active broker (roadmap B7)."""

from __future__ import annotations

import os
import re

import pytest
from _isolation import isolate_project_state  # noqa: F401
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    The command bootstraps config via load_env(), and python-dotenv writes
    the cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. At the repo root that leaks the developer's
    own .env (an empty APP_KEY line made later files' env:encrypt tests
    refuse). Snapshot before, restore after: identical pattern to the
    project fixture in test_env_crypt.
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


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


def _out(result) -> str:
    return ANSI_RE.sub("", result.output)


JOBS_MODULE = '''\
from fastplace.queue import Job


@Job()
async def ping(target: str = "localhost") -> None:
    """Ping a host."""
    ...
'''


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "app" / "jobs").mkdir(parents=True)
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "jobs" / "__init__.py").write_text(JOBS_MODULE)
    (tmp_path / "asgi.py").write_text("app = None\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("QUEUE_DRIVER", "memory")
    return tmp_path


def test_memory_driver_lists_dispatched_jobs(project):
    from fastplace.queue import import_jobs, queue, reset_queue

    root = project
    import_jobs(root)
    reset_queue()
    import asyncio

    asyncio.run(queue().dispatch("ping", target="example.com"))
    result = runner.invoke(cli_app, ["queue:jobs"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "ping" in out
    assert "queued" in out
    assert "example.com" in out


def test_status_filter_narrows(project):
    import asyncio

    from fastplace.queue import import_jobs, queue, reset_queue

    import_jobs(project)
    reset_queue()
    asyncio.run(queue().dispatch("ping", target="a"))
    result = runner.invoke(cli_app, ["queue:jobs", "--status", "active"])
    assert result.exit_code == 0, result.output
    assert "ping" not in _out(result)  # memory rows are all 'queued'


def test_unknown_status_is_a_usage_error(project):
    result = runner.invoke(cli_app, ["queue:jobs", "--status", "bogus"])
    assert result.exit_code == 1, result.output
    assert "unknown status" in _out(result)


def test_markup_payload_survives_escaped(project):
    import asyncio

    from fastplace.queue import import_jobs, queue, reset_queue

    import_jobs(project)
    reset_queue()
    asyncio.run(queue().dispatch("ping", target="[bold]boom[/bold]"))
    result = runner.invoke(cli_app, ["queue:jobs"])
    assert result.exit_code == 0, result.output
    assert "[bold]boom[/bold]" in _out(result)


def test_empty_broker_is_dim(project):
    result = runner.invoke(cli_app, ["queue:jobs"])
    assert result.exit_code == 0, result.output
    assert "no jobs" in _out(result)


def test_saq_driver_via_fake_iter_jobs(project, monkeypatch):
    import time

    soon = int(time.time()) + 5

    class _FakeJob:
        status = "queued"
        function = "ping"
        attempts = 0
        scheduled = soon  # saq stores absolute epoch seconds
        kwargs = {"target": "[bold]x[/bold]"}

    class _FakeSaqQueue:
        class queue:  # noqa: N801
            @staticmethod
            async def iter_jobs(statuses=None, batch_size=100):
                yield _FakeJob()

    monkeypatch.setattr("fastplace.queue.queue", lambda: _FakeSaqQueue())
    result = runner.invoke(cli_app, ["queue:jobs"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "ping" in out
    assert "[bold]x[/bold]" in out


def test_saq_scheduled_column_renders_epoch_seconds(project, monkeypatch):
    """Lock the unit: saq's Job.scheduled is absolute epoch SECONDS.

    Dividing by 1000 (as an early draft did) renders every scheduled job
    as January 1970 — this test derives the expected string from the same
    timestamp the fake carries, so a wrong unit changes the rendering and
    fails here.
    """
    import datetime as dt
    import time

    ts = int(time.time()) + 5

    class _FakeJob:
        status = "scheduled"
        function = "ping"
        attempts = 0
        scheduled = ts
        kwargs = {"target": "x"}

    class _FakeSaqQueue:
        class queue:  # noqa: N801
            @staticmethod
            async def iter_jobs(statuses=None, batch_size=100):
                yield _FakeJob()

    monkeypatch.setattr("fastplace.queue.queue", lambda: _FakeSaqQueue())
    result = runner.invoke(cli_app, ["queue:jobs"])
    assert result.exit_code == 0, result.output
    expected = dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    assert expected in _out(result)
