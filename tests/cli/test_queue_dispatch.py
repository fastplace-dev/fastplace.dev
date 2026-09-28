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


def test_dispatch_on_memory_driver_is_refused(project):
    """q1-G5 honesty: a CLI dispatch would land on this process's in-process
    queue and die with it — refuse instead of printing 'Dispatched'."""
    from fastplace.queue import import_jobs, queue, reset_queue

    import_jobs(project)
    reset_queue()
    result = runner.invoke(cli_app, ["queue:dispatch", "note", "--kwargs", '{"message": "m1"}'])
    assert result.exit_code == 1
    out = _out(result)
    assert "memory driver" in out
    assert "Dispatched" not in out
    store = queue()
    assert len(store.pending) == 0  # nothing was queued
    assert _system_jobs_module(project) == []  # and nothing ran


def test_dispatch_delay_on_memory_driver_refused_too(project):
    result = runner.invoke(cli_app, ["queue:dispatch", "note", "--delay", "5"])
    assert result.exit_code == 1
    assert "memory driver" in _out(result)


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


def test_saq_dispatch_builds_the_envelope(project, monkeypatch):
    """q1-G3 surface: --retries/--timeout/--delay/--queue ride the builder
    (queue().job(name, ...).dispatch(...)), and the printed line carries the
    job key so the dispatch is trackable afterwards."""
    from fastplace.queue import import_jobs

    import_jobs(project)
    captured: dict = {}

    class _Pending:
        def __init__(self, name, **options):
            captured["name"] = name
            captured["options"] = options

        async def dispatch(self, **kwargs):
            captured["kwargs"] = kwargs

            class _Handle:
                key = "job-abc"

            return _Handle()

    class _FakeSaqStore:
        def job(self, name, **options):
            return _Pending(name, **options)

    monkeypatch.setattr("fastplace.queue.queue", lambda: _FakeSaqStore())
    result = runner.invoke(
        cli_app,
        [
            "queue:dispatch",
            "note",
            "--kwargs",
            '{"message": "m2"}',
            "--retries",
            "5",
            "--timeout",
            "9",
            "--delay",
            "2",
            "--queue",
            "emails",
        ],
    )
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "Dispatched 'note'" in out
    assert "job-abc" in out
    assert "(in 2s)" in out
    assert captured["name"] == "note"
    assert captured["kwargs"] == {"message": "m2"}
    assert captured["options"]["retries"] == 5
    assert captured["options"]["timeout"] == 9.0
    assert captured["options"]["delay"] == 2.0
    assert captured["options"]["queue"] == "emails"


def test_saq_plain_dispatch_passes_no_overrides(project, monkeypatch):
    """No envelope flags → no builder overrides; every layer below (env,
    @Job, defaults) decides."""
    from fastplace.queue import import_jobs

    import_jobs(project)
    captured: dict = {}

    class _Pending:
        def __init__(self, name, **options):
            captured["name"] = name
            captured["options"] = options

        async def dispatch(self, **kwargs):
            captured["kwargs"] = kwargs

            class _Handle:
                key = "job-plain"

            return _Handle()

    class _FakeSaqStore:
        def job(self, name, **options):
            return _Pending(name, **options)

    monkeypatch.setattr("fastplace.queue.queue", lambda: _FakeSaqStore())
    result = runner.invoke(cli_app, ["queue:dispatch", "note"])
    assert result.exit_code == 0, result.output
    assert "Dispatched 'note'" in _out(result)
    assert captured["options"] == {"retries": None, "timeout": None, "delay": None, "queue": None}


def test_outside_project_exits_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["queue:dispatch", "note"])
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in _out(result)
