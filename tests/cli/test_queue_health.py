"""`queue:health` — queue stack diagnosis (roadmap spec, data plane)."""

from __future__ import annotations

import asyncio
import os
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from tests.cli._isolation import (  # noqa: F401 — autouse + fixture by name
    isolate_project_state,
    park_project_modules,
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
runner = CliRunner()


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them."""
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich console width so fix/detail hints never wrap mid-word."""
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture(autouse=True)
def _restored_config():
    """Rebind the process config singleton after each test (test_log_prune)."""
    import fastplace.config as config_module

    saved = config_module._default_config
    yield
    config_module._default_config = saved


@pytest.fixture(autouse=True)
def _reset_queue_singletons():
    """Drop process-wide queue/cache/failed-store/engine singletons.

    queue:health touches all four; a leaked singleton rooted at a dead tmp
    project would poison later suites (the cross-file pollution lesson).
    """
    yield
    from fastplace.cache import reset_cache
    from fastplace.orm.manager import reset_manager
    from fastplace.queue import reset_queue
    from fastplace.queue_failures import reset_failed_job_store

    reset_cache()
    reset_queue()
    reset_failed_job_store()
    reset_manager()


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


def _make_project(tmp_path, monkeypatch, env_text: str = "QUEUE_DRIVER=memory\n") -> object:
    """A cwd carrying the project marker + .env; chdir'd into."""
    monkeypatch.delenv("QUEUE_DRIVER", raising=False)
    (tmp_path / "asgi.py").write_text("# marker\n")
    if env_text:
        (tmp_path / ".env").write_text(env_text)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_queue_health_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "queue:health" in result.stdout


def test_queue_health_help_exits_zero():
    result = runner.invoke(cli_app, ["queue:health", "--help"])
    assert result.exit_code == 0
    assert "usage" in _out(result).lower()


def test_memory_project_all_checks_pass(tmp_path, monkeypatch):
    """Memory driver, no failed jobs, no schedule: exit 0, no FAIL rows."""
    _make_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_app, ["queue:health"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "driver" in out.lower()
    assert "FAIL" not in out


def test_pending_restart_sentinel_warns(tmp_path, monkeypatch):
    """A published restart sentinel is a WARN (workers exit at next boundary)."""
    from fastplace.cache import reset_cache
    from fastplace.queue import set_restart_sentinel

    _make_project(tmp_path, monkeypatch)
    reset_cache()
    asyncio.run(set_restart_sentinel())

    result = runner.invoke(cli_app, ["queue:health"])

    assert result.exit_code == 0, _out(result)  # WARN never fails the exit
    out = _out(result)
    assert "WARN" in out
    assert "restart" in out.lower()


def test_failed_jobs_warn(tmp_path, monkeypatch):
    """Recorded failures surface as WARN with the retry hint, exit stays 0."""
    from fastplace.queue_failures import record_failure

    _make_project(tmp_path, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./failed_probe.sqlite3")
    asyncio.run(record_failure("probe_job", {}, "boom"))

    result = runner.invoke(cli_app, ["queue:health"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "WARN" in out
    assert "queue:retry" in out  # the exact fix command


def test_unknown_queue_driver_fails(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="QUEUE_DRIVER=kafka\n")

    result = runner.invoke(cli_app, ["queue:health"])

    assert result.exit_code == 1
    out = _out(result)
    assert "kafka" in out.lower() or "unknown" in out.lower()


def test_defaults_row_shows_the_effective_envelope(tmp_path, monkeypatch):
    """q1-G3 surfacing: one informational row shows the envelope every
    undecorated dispatch would carry — env overrides visible at a glance."""
    for key in ("QUEUE_TRIES", "QUEUE_TIMEOUT", "QUEUE_BACKOFF", "QUEUE_TTL"):
        monkeypatch.delenv(key, raising=False)
    _make_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_app, ["queue:health"])

    out = _out(result)
    assert result.exit_code == 0, out
    assert "defaults" in out
    assert "retries=3" in out  # framework defaults
    assert "timeout=60" in out

    _make_project(tmp_path, monkeypatch, env_text="QUEUE_DRIVER=memory\nQUEUE_TRIES=5\n")
    result = runner.invoke(cli_app, ["queue:health"])
    out = _out(result)
    assert result.exit_code == 0, out
    assert "retries=5" in out  # env override surfaced


def test_schedule_file_counts_tasks(tmp_path, monkeypatch, park_project_modules):  # noqa: F811 — fixture param
    """A loadable app/schedule.py with one task passes with the task count."""
    root = _make_project(tmp_path, monkeypatch)
    (root / "app").mkdir()
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "schedule.py").write_text(
        "from fastplace.schedule import Schedule\n"
        "\n"
        "\n"
        "def _job() -> None:\n"
        "    pass\n"
        "\n"
        "\n"
        "def schedule(s: Schedule) -> None:\n"
        '    s.call(_job, name="probe").every_minutes(5)\n'
    )

    result = runner.invoke(cli_app, ["queue:health"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "FAIL" not in out
    assert "1 task" in out


def test_broken_schedule_fails(tmp_path, monkeypatch, park_project_modules):  # noqa: F811 — fixture param
    """Broken project code must fail loud at load, not on the first tick."""
    root = _make_project(tmp_path, monkeypatch)
    (root / "app").mkdir()
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "schedule.py").write_text("def schedule(s: Schedule) -> None:\n    s.nope()\n")

    result = runner.invoke(cli_app, ["queue:health"])

    assert result.exit_code == 1
    out = _out(result)
    assert "FAIL" in out
    assert "schedule" in out.lower()


# ---------------------------------------------------------------------------
# aborted-job visibility (q2-G5) — crash-loss surfaces as its own WARN row
# ---------------------------------------------------------------------------


class _FakeAbortedDriver:
    """The saq driver surface the aborted check reads: aborted_jobs()."""

    def __init__(self, jobs: list) -> None:
        self._jobs = jobs

    async def aborted_jobs(self) -> list:
        return self._jobs


def test_aborted_check_passes_on_memory_driver(tmp_path, monkeypatch):
    """Nothing to scan in-process — the row states that honestly."""
    from fastplace.cli.queue import _check_aborted_jobs

    _make_project(tmp_path, monkeypatch)
    check = _check_aborted_jobs()
    assert check.name == "aborted"
    assert check.status == "pass"
    assert "memory" in check.detail


def test_aborted_check_warns_when_redis_holds_aborted_jobs(tmp_path, monkeypatch):
    """q2-G5: jobs a crashed worker left ABORTED in redis are a WARN with the
    queue:failed hint — silent crash-loss is the bug this row exists for."""
    from fastplace.cli.queue import _check_aborted_jobs

    _make_project(tmp_path, monkeypatch)
    # The factory seam: the check resolves the driver through fastplace.queue's
    # queue() — env stays memory so the redis check keeps skipping.
    import fastplace.queue as queue_module

    monkeypatch.setattr(queue_module, "queue", lambda: _FakeAbortedDriver(["a", "b"]))
    check = _check_aborted_jobs()
    assert check.status == "warn"
    assert "2" in check.detail  # the count is the story
    assert "queue:failed" in check.fix  # and where to see them


def test_aborted_check_survives_an_unreachable_redis(tmp_path, monkeypatch):
    """A scan failure is a WARN finding, never a crash of the doctor."""
    from fastplace.cli.queue import _check_aborted_jobs

    _make_project(tmp_path, monkeypatch)
    import fastplace.queue as queue_module

    class _BrokenDriver:
        async def aborted_jobs(self):
            raise RuntimeError("redis gone")

    monkeypatch.setattr(queue_module, "queue", lambda: _BrokenDriver())
    check = _check_aborted_jobs()
    assert check.status == "warn"
    assert "cannot" in check.detail.lower()
