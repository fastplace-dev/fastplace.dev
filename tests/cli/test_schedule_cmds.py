"""``fastplace schedule:list`` / ``schedule:run`` (spec #57, #59)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from _isolation import park_app_modules
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Rich colorizes when the environment forces color; strip codes before matching.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()

#: A frozen timestamp whose minute (10) is a multiple of 5 but not of 7 —
#: `every_minutes(5)` due, `every_minutes(7)` not.
FROZEN_NOW = "2026-01-15T10:10:00"


@pytest.fixture
def fresh_project(tmp_path, monkeypatch):
    """A freshly scaffolded project cwd; env/config and the app.* slice restored.

    ``schedule:list``/``schedule:run`` import the fixture project's
    ``app/schedule.py``, which adopts the tmp project's ``app.*`` entries into
    the module cache — ``park_app_modules`` sweeps them and restores the
    pre-test slice so later suites keep finding theirs cached.
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
    # workaround tests/cli/test_list_introspection.py applies.
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


# --- schedule:list -------------------------------------------------------------


def test_schedule_list_shows_names_expressions_and_next_due(fresh_project):
    _define_schedule(
        fresh_project,
        "def schedule(s):\n"
        "    s.command('db:seed').daily_at('02:30')\n"
        "    s.command('cache:clear').every_minutes(5)\n",
    )
    code, out = _run("schedule:list", "--now", FROZEN_NOW)
    assert code == 0, out
    assert "db:seed" in out
    assert "cache:clear" in out
    assert "daily at 02:30" in out
    assert "every 5 minutes" in out
    # next-due from 10:10: the daily task rolls to tomorrow, the 5-minute one to 10:15
    assert "2026-01-16 02:30" in out
    assert "10:15" in out


def test_schedule_list_without_schedule_file_is_friendly_empty_state(fresh_project):
    code, out = _run("schedule:list")
    assert code == 0, out
    assert "no scheduled tasks" in out


def test_schedule_list_rejects_malformed_now(fresh_project):
    _define_schedule(fresh_project, "def schedule(s):\n    s.command('x').daily()\n")
    code, out = _run("schedule:list", "--now", "not-a-timestamp")
    assert code == 1
    assert "timestamp" in out


# --- schedule:run --------------------------------------------------------------


@pytest.fixture
def command_recorder(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "fastplace.schedule._subprocess_runner",
        lambda command: calls.append(command) or 0,
    )
    return calls


def test_schedule_run_executes_only_the_due_task(fresh_project, command_recorder):
    _define_schedule(
        fresh_project,
        "def schedule(s):\n"
        "    s.command('db:seed').every_minutes(5)\n"
        "    s.command('cache:clear').every_minutes(7)\n",
    )
    code, out = _run("schedule:run", "--now", FROZEN_NOW)
    assert code == 0, out
    assert command_recorder == ["db:seed"]  # 10 % 7 != 0 — cache:clear stays idle
    assert "ran db:seed" in out
    assert "cache:clear" not in out


def test_schedule_run_reports_nothing_due(fresh_project, command_recorder):
    _define_schedule(
        fresh_project,
        "def schedule(s):\n    s.command('cache:clear').every_minutes(7)\n",
    )
    code, out = _run("schedule:run", "--now", FROZEN_NOW)
    assert code == 0, out
    assert command_recorder == []
    assert "no scheduled tasks due" in out


def test_schedule_run_failure_exits_one_with_the_error(fresh_project, monkeypatch):
    def exploding_runner(command: str) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr("fastplace.schedule._subprocess_runner", exploding_runner)
    _define_schedule(
        fresh_project,
        "def schedule(s):\n    s.command('db:seed').every_minutes(5)\n",
    )
    code, out = _run("schedule:run", "--now", FROZEN_NOW)
    assert code == 1
    assert "failed" in out
    assert "db:seed" in out
    assert "boom" in out


# --- outside-project guard -----------------------------------------------------


@pytest.mark.parametrize("command", [["schedule:list"], ["schedule:run"]])
def test_schedule_commands_outside_a_project_fail_friendly(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, command)
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in ANSI_RE.sub("", result.output)
