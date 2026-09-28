"""q2-G2/q2-G5 — queue:work (saq branch) runs the aborted-job scan alongside
the worker: one pass at startup (losses from a previous crash surface
immediately) and a companion task every 60s while the worker runs. The scan
is best-effort — a failing scan logs a warning, never kills the worker.
"""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _out(result) -> str:
    return ANSI_RE.sub("", result.output)


@pytest.fixture(autouse=True)
def _fresh_state():
    from fastplace.cache import reset_cache
    from fastplace.queue import reset_queue, reset_registry
    from fastplace.queue_failures import reset_failed_job_store

    reset_registry()
    reset_queue()
    reset_failed_job_store()
    reset_cache()
    yield
    reset_registry()
    reset_queue()
    reset_failed_job_store()
    reset_cache()


class _FakeWorker:
    async def start(self) -> None:
        return None


@pytest.fixture
def saq_store(monkeypatch):
    """A fake saq driver the CLI's queue:work can drive: build_worker returns
    a worker whose start() returns, record_aborted_jobs is scriptable."""
    import fastplace.queue as queue_module

    class _FakeSaqDriver:
        def __init__(self) -> None:
            self.recorded_calls = 0
            self.raises: Exception | None = None

        def build_worker(self, **kwargs):
            return _FakeWorker()

        async def record_aborted_jobs(self):
            self.recorded_calls += 1
            if self.raises is not None:
                raise self.raises
            return ["export.csv"] if self.recorded_calls == 1 else []

    fake = _FakeSaqDriver()
    monkeypatch.setenv("QUEUE_DRIVER", "saq")
    monkeypatch.setattr(queue_module, "queue", lambda: fake)
    return fake


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_work_reports_startup_scan_losses(project, saq_store):
    """One pass before the worker starts: crash-losses from the previous
    worker's death surface in this run's output, not a minute later."""
    result = runner.invoke(cli_app, ["queue:work"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert saq_store.recorded_calls >= 1  # the startup pass ran
    assert "aborted" in out.lower()
    assert "export.csv" in out  # the lost job is named
    assert "queue:failed" in out  # and the command that manages it


def test_work_scan_failure_never_kills_the_worker(project, saq_store):
    saq_store.raises = RuntimeError("redis flaked")
    result = runner.invoke(cli_app, ["queue:work"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "scan" in out.lower()  # the failure is visible as a warning…
    assert "Traceback" not in out  # …not a crash


def test_work_clean_startup_scan_prints_no_loss_line(project, saq_store):
    saq_store.recorded_calls = 1  # startup pass will find nothing new
    result = runner.invoke(cli_app, ["queue:work"])
    assert result.exit_code == 0, result.output
    assert "aborted job" not in _out(result).lower()
