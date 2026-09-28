"""queue:list — registry table of discovered job handlers (roadmap A6)."""

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
    """Confine os.environ changes to the test that caused them."""
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
async def resize_image(path: str, size: int = 64) -> None:
    """Resize an uploaded image."""
    ...


@Job()
async def send_invoice(order_id: int) -> None:
    ...


@Job(name="purge_temp", retries=2, timeout=45)
async def _purge_temp_files() -> None:
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


def test_lists_registered_handlers_with_module_and_signature(project):
    result = runner.invoke(cli_app, ["queue:list"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "resize_image" in out
    assert "send_invoice" in out
    assert "app.jobs" in out
    assert "path" in out and "size" in out  # signature rendered
    assert "Resize an uploaded image." in out  # docstring first line


def test_names_the_active_driver(project, monkeypatch):
    monkeypatch.setenv("QUEUE_DRIVER", "memory")
    result = runner.invoke(cli_app, ["queue:list"])
    assert result.exit_code == 0, result.output
    assert "memory" in _out(result)


def test_lists_each_jobs_effective_envelope(project, monkeypatch):
    """q1-G3 surfacing: the envelope column shows what a dispatch of each
    job would carry — @Job params over the framework defaults, and env
    overrides visible without decoding three layers by hand."""
    for key in ("QUEUE_TRIES", "QUEUE_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)

    result = runner.invoke(cli_app, ["queue:list"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "3×60s" in out  # framework defaults (retries × timeout)
    assert "2×45s" in out  # @Job(retries=2, timeout=45) wins for purge_temp

    monkeypatch.setenv("QUEUE_TRIES", "5")
    result = runner.invoke(cli_app, ["queue:list"])
    assert result.exit_code == 0, result.output
    assert "5×60s" in _out(result)  # env raises the floor for undecorated jobs
    assert "2×45s" in _out(result)  # @Job params still win


def test_outside_project_exits_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no asgi.py anywhere up the tree
    result = runner.invoke(cli_app, ["queue:list"])
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in _out(result)


def test_empty_registry_is_a_dim_note(tmp_path, monkeypatch):
    (tmp_path / "app" / "jobs").mkdir(parents=True)
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "jobs" / "__init__.py").write_text("")
    (tmp_path / "asgi.py").write_text("app = None\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    result = runner.invoke(cli_app, ["queue:list"])
    assert result.exit_code == 0, result.output
    assert "no registered jobs" in _out(result)


# ---------------------------------------------------------------------------
# review F7 — a misconfigured envelope env must be a red line, not a traceback
# ---------------------------------------------------------------------------


def test_misconfigured_envelope_env_is_a_red_line_not_a_traceback(project, monkeypatch):
    """``QUEUE_TIMEOUT=0`` makes ``effective_job_options`` raise
    ``ConfigurationError`` mid-table — the command must exit 1 with a red
    config-error line, never spill a traceback at the operator."""
    monkeypatch.setenv("QUEUE_TIMEOUT", "0")

    result = runner.invoke(cli_app, ["queue:list"])
    assert result.exit_code == 1
    out = _out(result)
    assert "QUEUE_TIMEOUT" in out
    # A traceback is the failure mode this guards against — none may render.
    assert "Traceback" not in out
