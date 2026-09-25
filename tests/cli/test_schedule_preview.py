"""``fastplace schedule:preview`` — firing simulation (roadmap C1)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from _isolation import isolate_project_state, park_app_modules  # noqa: F401
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Rich colorizes when the environment forces color; strip codes before matching.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()

#: Frozen at minute 10 — a 5-minute heartbeat next fires at 10:15, a daily
#: 02:30 task rolls to tomorrow.
FROZEN_NOW = "2026-01-15T10:10:00"


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them."""
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture
def fresh_project(tmp_path, monkeypatch):
    """A freshly scaffolded project cwd; env/config and the app.* slice restored.

    ``schedule:preview`` imports the fixture project's ``app/schedule.py``,
    which adopts the tmp project's ``app.*`` entries into the module cache —
    ``park_app_modules`` sweeps them and restores the pre-test slice so later
    suites keep finding theirs cached. (Mirrors test_schedule_cmds.py.)
    """
    cwd_before = Path.cwd().resolve()
    env_before = dict(os.environ)

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["new", "blog"])
    assert result.exit_code == 0, result.output

    root = tmp_path / "blog"
    # The scaffold's app/ is a namespace package; a regular package anywhere
    # else on sys.path (this repo's own app/) would win resolution and shadow
    # the fixture project's schedule file. Make it regular — the same
    # workaround tests/cli/test_schedule_cmds.py applies.
    (root / "app" / "__init__.py").write_text("")
    monkeypatch.chdir(root)
    try:
        with park_app_modules():
            yield root
    finally:
        os.environ.clear()
        os.environ.update(env_before)
        from fastplace.config import reset_config

        reset_config(cwd_before)


def _define_schedule(root: Path, body: str) -> None:
    (root / "app" / "schedule.py").write_text(body)


def _run(*args):
    result = runner.invoke(cli_app, list(args))
    return result.exit_code, ANSI_RE.sub("", result.output)


TWO_TASKS = (
    "def schedule(s):\n"
    "    s.command('nightly_report').daily_at('02:30')\n"
    "    s.command('heartbeat').every_minutes(5)\n"
)

NEVER_CRON = (
    "def schedule(s):\n"
    "    s.command('ok_daily').cron('0 4 * * *')\n"
    "    s.command('never').cron('0 0 30 2 *')\n"
)


def test_chronological_rows_and_per_task_cap(fresh_project):
    _define_schedule(fresh_project, TWO_TASKS)
    code, out = _run("schedule:preview", "--now", FROZEN_NOW, "--hours", "24", "--per-task", "3")
    assert code == 0, out
    assert "nightly_report" in out
    assert "heartbeat" in out
    assert "+ more within window" in out  # every-5-minute task capped at 3
    assert "2026-01-16 02:30" in out  # tomorrow's firing


def test_mixed_schedule_is_chronological(fresh_project):
    _define_schedule(fresh_project, TWO_TASKS)
    code, out = _run("schedule:preview", "--now", FROZEN_NOW, "--hours", "1")
    assert code == 0, out
    stamps = [
        stamp
        for line in out.splitlines()
        if "heartbeat" in line
        for stamp in re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", line)
    ]
    assert stamps  # at least one heartbeat within the hour
    assert stamps == sorted(stamps)  # the table's rows are chronological


def test_never_matching_cron_flags_and_continues(fresh_project):
    _define_schedule(fresh_project, NEVER_CRON)
    code, out = _run("schedule:preview", "--now", FROZEN_NOW, "--hours", "24")
    assert code == 0, out  # the healthy task still previews
    assert "never matches a real date" in out
    assert "0 0 30 2 *" in out
    assert "ok_daily" in out


def test_task_with_no_firings_in_window(fresh_project):
    _define_schedule(
        fresh_project,
        TWO_TASKS.replace("daily_at('02:30')", "daily_at('03:00')"),
    )
    code, out = _run("schedule:preview", "--now", FROZEN_NOW, "--hours", "1")
    assert code == 0, out
    assert "no firings" in out  # 03:00 lies outside the 10:10-11:10 window


def test_sim_runner_never_fires(fresh_project, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "fastplace.schedule._subprocess_runner",
        lambda command: calls.append(command) or 0,
    )
    _define_schedule(fresh_project, TWO_TASKS)
    code, out = _run("schedule:preview", "--now", FROZEN_NOW)
    assert code == 0, out
    assert calls == []  # previewing simulates; nothing ever executes


def test_empty_schedule_is_dim(fresh_project):
    _define_schedule(fresh_project, "def schedule(s):\n    pass\n")
    code, out = _run("schedule:preview", "--now", FROZEN_NOW)
    assert code == 0, out
    assert "no scheduled tasks" in out


def test_invalid_now_exits_one(fresh_project):
    _define_schedule(fresh_project, TWO_TASKS)
    code, out = _run("schedule:preview", "--now", "not-a-date")
    assert code == 1
    assert "invalid timestamp" in out
