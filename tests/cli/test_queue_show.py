"""queue:show — one failed job at full fidelity (roadmap B8)."""

from __future__ import annotations

import asyncio
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


@Job()
async def resize(path: str = "x.png") -> None:
    """Resize an image."""
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
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/x.db")
    return tmp_path


def _seed_failed(name: str = "resize", error: str = "boom") -> int:
    from fastplace.queue_failures import failed_job_store, reset_failed_job_store

    reset_failed_job_store()
    store = failed_job_store()

    async def _record():
        return await store.record(name=name, kwargs={"path": "a.png"}, error=error)

    return asyncio.run(_record())


def test_shows_every_field_and_full_error(project):
    job_id = _seed_failed(error="ValueError: boom at frame 42")
    result = runner.invoke(cli_app, ["queue:show", str(job_id)])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "resize" in out
    assert "a.png" in out
    assert "ValueError: boom at frame 42" in out  # full untruncated error
    assert "registered" in out
    assert f"queue:retry {job_id}" in out


def test_markup_in_error_survives_escaped(project):
    job_id = _seed_failed(name="resize", error="[bold]evil[/bold]")
    result = runner.invoke(cli_app, ["queue:show", str(job_id)])
    assert result.exit_code == 0, result.output
    assert "[bold]evil[/bold]" in _out(result)


def test_unknown_id_exits_one(project):
    result = runner.invoke(cli_app, ["queue:show", "999"])
    assert result.exit_code == 1
    assert "no failed job #999" in _out(result)


# Review Focus 4 — pinned here.
def test_non_numeric_id_is_a_red_message(project):
    result = runner.invoke(cli_app, ["queue:show", "abc"])
    assert result.exit_code == 1
    out = _out(result)
    assert "'abc' is not a job id" in out
    assert "Traceback" not in out


def test_unregistered_handler_flags_yellow(project, monkeypatch):
    job_id = _seed_failed(name="ghost_job")

    async def _empty_import(root):  # registry without ghost_job
        return []

    monkeypatch.setattr("fastplace.queue.import_jobs", _empty_import)
    result = runner.invoke(cli_app, ["queue:show", str(job_id)])
    assert result.exit_code == 0, result.output
    assert "no longer registered" in _out(result)
