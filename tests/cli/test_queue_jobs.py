"""queue:jobs — list jobs on the active broker (roadmap B7)."""

from __future__ import annotations

import os
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from tests.cli._isolation import isolate_project_state  # noqa: F401

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


# Review fix 2 — --status must speak saq's real Status enum; "scheduled"
# and "incomplete" are CLI concepts the command translates, not broker
# statuses a filter can match.
class _Job:
    def __init__(self, status, function, scheduled=0):
        self.status = status
        self.function = function
        self.attempts = 0
        self.scheduled = scheduled  # saq: absolute epoch seconds
        self.kwargs = {}


class _FakeBroker:
    """Filters like the real broker (job.status in set(statuses)) and
    records exactly what the command asked it to match on."""

    def __init__(self, jobs):
        self._jobs = jobs
        self.seen = None

    async def iter_jobs(self, statuses=None, batch_size=100):
        self.seen = set(statuses or ())
        for job in self._jobs:
            if job.status in self.seen:
                yield job


class _RecordingSaqQueue:
    def __init__(self, jobs):
        self.queue = _FakeBroker(jobs)


def _install_fake(monkeypatch, jobs):
    store = _RecordingSaqQueue(jobs)
    monkeypatch.setattr("fastplace.queue.queue", lambda: store)
    return store.queue


def test_saq_status_filter_passes_real_enums(project, monkeypatch):
    from saq.job import Status

    broker = _install_fake(monkeypatch, [_Job("queued", "ping")])
    result = runner.invoke(cli_app, ["queue:jobs", "--status", "queued"])
    assert result.exit_code == 0, result.output
    assert broker.seen == {Status.QUEUED}
    assert all(isinstance(s, Status) for s in broker.seen)


def test_saq_incomplete_maps_to_unfinished_statuses(project, monkeypatch):
    from saq.job import Status

    broker = _install_fake(monkeypatch, [_Job("queued", "ping")])
    result = runner.invoke(cli_app, ["queue:jobs", "--status", "incomplete"])
    assert result.exit_code == 0, result.output
    assert broker.seen == {Status.NEW, Status.QUEUED, Status.ACTIVE, Status.ABORTING}
    assert "ping" in _out(result)


def test_saq_scheduled_requests_undone_statuses_never_scheduled(project, monkeypatch):
    from saq.job import Status

    broker = _install_fake(monkeypatch, [])
    result = runner.invoke(cli_app, ["queue:jobs", "--status", "scheduled"])
    assert result.exit_code == 0, result.output
    # "scheduled" is not a saq Status — the broker must be asked for the
    # undone statuses, and the epoch post-filter narrows from there.
    assert broker.seen <= {Status.NEW, Status.QUEUED, Status.ACTIVE}
    assert "scheduled" not in {str(s) for s in broker.seen}


def test_saq_scheduled_filter_keeps_only_future_fire_times(project, monkeypatch):
    import time

    future = int(time.time()) + 300
    broker = _install_fake(
        monkeypatch,
        [
            _Job("queued", "later_ping", scheduled=future),
            _Job("queued", "immediate_ping", scheduled=0),
            _Job("active", "running_ping", scheduled=future),
        ],
    )
    result = runner.invoke(cli_app, ["queue:jobs", "--status", "scheduled"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "later_ping" in out
    assert "immediate_ping" not in out  # due now — not scheduled
    assert "running_ping" not in out  # already running — not scheduled
    assert broker.seen is not None
