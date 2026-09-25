"""queue:dispatch — enqueue a registered handler from the CLI (roadmap B9)."""

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

    cache:clear/cache:forget bootstrap config via load_env(), and
    python-dotenv writes the cwd .env's keys straight into the REAL
    os.environ — a mutation no monkeypatch sees or undoes. At the repo
    root that leaks the developer's own .env (an empty APP_KEY line made
    later files' env:encrypt tests refuse). Snapshot before, restore
    after: identical pattern to the project fixture in test_env_crypt.
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

RAN = []


@Job()
async def note(message: str = "hi") -> None:
    """Record a message."""
    RAN.append(message)
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


def _system_jobs_module(project):
    """Read RAN out of the project's jobs module after a dispatch."""
    import importlib
    import sys

    sys.path.insert(0, str(project))
    try:
        module = importlib.import_module("app.jobs")
        return list(module.RAN)
    finally:
        sys.path.remove(str(project))


def test_dispatch_enqueues_without_running(project):
    from fastplace.queue import import_jobs, queue, reset_queue

    import_jobs(project)
    reset_queue()
    result = runner.invoke(cli_app, ["queue:dispatch", "note", "--kwargs", '{"message": "m1"}'])
    assert result.exit_code == 0, result.output
    assert "Dispatched 'note'" in _out(result)
    store = queue()
    assert len(store.pending) == 1
    assert store.pending[0].kwargs == {"message": "m1"}
    assert _system_jobs_module(project) == []  # dispatch only — handler never ran


def test_unknown_job_refused(project):
    result = runner.invoke(cli_app, ["queue:dispatch", "ghost"])
    assert result.exit_code == 1
    out = _out(result)
    assert "unknown job 'ghost'" in out
    assert "queue:list" in out


def test_bad_kwargs_json_refused(project):
    result = runner.invoke(cli_app, ["queue:dispatch", "note", "--kwargs", "{nope"])
    assert result.exit_code == 1
    assert "Traceback" not in _out(result)


def test_non_object_kwargs_refused(project):
    result = runner.invoke(cli_app, ["queue:dispatch", "note", "--kwargs", "[1]"])
    assert result.exit_code == 1
    assert "object" in _out(result)


def test_delay_on_memory_driver_refused(project):
    from fastplace.queue import import_jobs, reset_queue

    import_jobs(project)
    reset_queue()
    result = runner.invoke(cli_app, ["queue:dispatch", "note", "--delay", "5"])
    assert result.exit_code == 1
    assert "cannot schedule delayed jobs" in _out(result)
    from fastplace.queue import queue

    assert len(queue().pending) == 0


def test_saq_delay_routes_through_dispatch_delayed(project, monkeypatch):
    recorded = {}

    class _FakeSaqQueue:
        async def dispatch(self, name, **kwargs):
            recorded["dispatch"] = (name, kwargs)

        async def dispatch_delayed(self, name, kwargs=None, delay=1.0):
            recorded["delayed"] = (name, kwargs, delay)

    monkeypatch.setattr("fastplace.queue.queue", lambda: _FakeSaqQueue())
    result = runner.invoke(
        cli_app, ["queue:dispatch", "note", "--kwargs", '{"message": "m2"}', "--delay", "5"]
    )
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "Dispatched 'note'" in out
    assert "(in 5.0s)" in out
    assert recorded == {"delayed": ("note", {"message": "m2"}, 5.0)}


def test_outside_project_exits_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["queue:dispatch", "note"])
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in _out(result)
