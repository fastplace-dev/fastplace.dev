"""``fastplace schedule:work`` / ``schedule:test`` (spec #58, #60)."""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.schedule import Schedule, TaskResult, run_worker
from tests.cli._isolation import park_app_modules

# Rich colorizes when the environment forces color; strip codes before matching.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


@pytest.fixture
def fresh_project(tmp_path, monkeypatch):
    """A freshly scaffolded project cwd; env/config and the app.* slice restored.

    Same shape as tests/cli/test_schedule_cmds.py: the worker/test commands
    import the fixture project's ``app/schedule.py``, which adopts the tmp
    project's ``app.*`` entries into the module cache — ``park_app_modules``
    sweeps them and restores the pre-test slice so later suites keep finding
    theirs cached.
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


@pytest.fixture
def command_recorder(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "fastplace.schedule._subprocess_runner",
        lambda command: calls.append(command) or 0,
    )
    return calls


class FakeClock:
    """Returns ``start`` on the first call, then advances ``step`` per call."""

    def __init__(self, start: dt.datetime, step: dt.timedelta = dt.timedelta(minutes=1)) -> None:
        self._next = start
        self._step = step

    def __call__(self) -> dt.datetime:
        now = self._next
        self._next = now + self._step
        return now


def _recording_sleep(records: list[float]) -> Any:
    """An async no-op sleep that records the seconds it was asked to wait."""

    async def _sleep(seconds: float) -> None:
        records.append(seconds)

    return _sleep


def _noop_sleep() -> Any:
    async def _sleep(seconds: float) -> None:
        return None

    return _sleep


# --- the worker loop (driven directly, fake clock) ------------------------------


def test_worker_runs_tasks_on_the_right_ticks():
    fired: list[str] = []
    schedule = Schedule()
    schedule.call(lambda: fired.append("emails"), name="emails").every_minutes(5)
    clock = FakeClock(dt.datetime(2026, 1, 15, 10, 10))

    asyncio.run(run_worker(schedule, clock=clock, sleep_fn=_noop_sleep(), stop_after=6))

    # Ticks 10:10 .. 10:15 — due at 10:10 and 10:15, idle in between.
    assert fired == ["emails", "emails"]


def test_worker_survives_a_failing_task():
    fired: list[str] = []

    def _boom() -> None:
        raise RuntimeError("boom")

    schedule = Schedule()
    schedule.call(_boom, name="bad").every_minutes(1)
    schedule.call(lambda: fired.append("good"), name="good").every_minutes(1)
    clock = FakeClock(dt.datetime(2026, 1, 15, 10, 10))

    asyncio.run(run_worker(schedule, clock=clock, sleep_fn=_noop_sleep(), stop_after=2))

    # Both ticks ran — the failing task never killed the loop.
    assert fired == ["good", "good"]


def test_worker_sleeps_to_the_next_minute_boundary():
    records: list[float] = []
    schedule = Schedule()
    schedule.call(lambda: None, name="noop").every_minutes(1)
    clock = FakeClock(dt.datetime(2026, 1, 15, 10, 10, 30), step=dt.timedelta())

    asyncio.run(run_worker(schedule, clock=clock, sleep_fn=_recording_sleep(records), stop_after=2))

    # 10:10:30 waits exactly 30s to the 10:11:00 boundary; the final tick
    # stops without a trailing sleep.
    assert records == [30.0]


def test_worker_reports_each_tick_through_on_tick():
    ticks: list[list[str]] = []
    schedule = Schedule()
    schedule.call(lambda: "ok", name="a").every_minutes(1)

    def on_tick(results: list[TaskResult]) -> None:
        ticks.append([f"{result.task.name}:{result.ok}" for result in results])

    clock = FakeClock(dt.datetime(2026, 1, 15, 10, 10))
    asyncio.run(
        run_worker(schedule, clock=clock, sleep_fn=_noop_sleep(), stop_after=2, on_tick=on_tick)
    )

    assert ticks == [["a:True"], ["a:True"]]


# --- schedule:work --------------------------------------------------------------


def test_schedule_work_runs_until_interrupted(fresh_project, monkeypatch):
    _define_schedule(
        fresh_project,
        "def schedule(s):\n    s.command('db:seed').every_minutes(5)\n",
    )

    async def fake_worker(schedule_obj, **kwargs):
        task = schedule_obj.find("db:seed")
        kwargs["on_tick"]([TaskResult(task, ok=True, value=3)])
        raise KeyboardInterrupt

    monkeypatch.setattr("fastplace.schedule.run_worker", fake_worker)
    code, out = _run("schedule:work")
    assert code == 0, out
    assert "ran db:seed — 3" in out
    assert "worker stopped" in out


def test_schedule_work_empty_schedule_is_friendly(fresh_project):
    code, out = _run("schedule:work")
    assert code == 0, out
    assert "no scheduled tasks" in out


# --- schedule:test --------------------------------------------------------------


def test_schedule_test_runs_task_regardless_of_dueness(fresh_project, command_recorder):
    _define_schedule(
        fresh_project,
        "def schedule(s):\n    s.command('emails').daily_at('02:30')\n",
    )
    code, out = _run("schedule:test", "emails")
    assert code == 0, out
    assert command_recorder == ["emails"]
    assert "ran emails" in out


def test_schedule_test_unknown_name_lists_known_tasks(fresh_project):
    _define_schedule(
        fresh_project,
        "def schedule(s):\n"
        "    s.command('emails').daily_at('02:30')\n"
        "    s.command('db:seed').daily()\n",
    )
    code, out = _run("schedule:test", "nope")
    assert code == 1
    assert "unknown task" in out
    assert "emails" in out
    assert "db:seed" in out


def test_schedule_test_on_empty_schedule_is_unknown_task(fresh_project):
    code, out = _run("schedule:test", "anything")
    assert code == 1
    assert "unknown task" in out


def test_schedule_test_failure_exits_one_with_the_error(fresh_project, monkeypatch):
    def exploding_runner(command: str) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr("fastplace.schedule._subprocess_runner", exploding_runner)
    _define_schedule(
        fresh_project,
        "def schedule(s):\n    s.command('emails').daily()\n",
    )
    code, out = _run("schedule:test", "emails")
    assert code == 1
    assert "failed" in out
    assert "emails" in out
    assert "boom" in out


# --- outside-project guard ------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [["schedule:work"], ["schedule:test", "x"]],
)
def test_schedule_worker_commands_outside_a_project_fail_friendly(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, command)
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in ANSI_RE.sub("", result.output)
