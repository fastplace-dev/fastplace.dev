"""The scheduled-task registry — expressions, cron matching, execution (spec #57)."""

from __future__ import annotations

import asyncio
import datetime as dt
import sys

import pytest

from fastplace.schedule import Schedule, ScheduledTask, load_schedule

# --- registration --------------------------------------------------------------


def test_command_every_minutes_registers_named_task():
    schedule = Schedule()
    task = schedule.command("db:seed").every_minutes(5)

    assert [t.name for t in schedule.tasks()] == ["db:seed"]
    assert task.command == "db:seed"
    assert task.callback is None
    assert task.every == "5 minutes"
    assert task.at is None
    assert task.expression == "every 5 minutes"


def test_call_daily_at_registers_callback_task():
    schedule = Schedule()

    def nightly() -> None: ...

    task = schedule.call(nightly).daily_at("02:30")

    assert [t.name for t in schedule.tasks()] == ["nightly"]
    assert task.callback is nightly
    assert task.command is None
    assert task.every == "day"
    assert task.at == "02:30"
    assert task.expression == "daily at 02:30"


def test_daily_defaults_to_midnight_and_hourly_fires_on_the_hour():
    schedule = Schedule()
    daily = schedule.command("backups").daily()
    hourly = schedule.command("ping").hourly()

    assert daily.at == "00:00"
    assert daily.expression == "daily"
    assert hourly.every == "hour"
    assert hourly.expression == "hourly"


def test_cron_task_carries_the_raw_expression():
    schedule = Schedule()
    task = schedule.command("report").cron("30 2 * * *")

    assert task.every == "cron"
    assert task.expression == "30 2 * * *"


def test_task_requires_exactly_one_of_command_or_callback():
    with pytest.raises(ValueError):
        ScheduledTask("orphan")
    with pytest.raises(ValueError):
        ScheduledTask("greedy", command="db:seed", callback=lambda: None)


def test_task_rejects_inconsistent_frequency_arguments():
    # Direct construction must fail loudly at init, not with a
    # ZeroDivisionError inside is_due on the first tick.
    with pytest.raises(ValueError):
        ScheduledTask("no-cron", command="x", every="cron")
    with pytest.raises(ValueError):
        ScheduledTask("dayless", command="x", every="day")
    with pytest.raises(ValueError):
        ScheduledTask("odd", command="x", every="fortnights")
    with pytest.raises(ValueError):
        ScheduledTask("bad-minutes", command="x", every="x minutes")


# --- every_minutes -------------------------------------------------------------


def test_every_minutes_due_at_five_minute_boundaries():
    task = Schedule().command("tick").every_minutes(5)

    assert task.is_due(dt.datetime(2026, 1, 15, 10, 0))
    assert task.is_due(dt.datetime(2026, 1, 15, 10, 5))
    assert task.is_due(dt.datetime(2026, 1, 15, 10, 30))
    assert not task.is_due(dt.datetime(2026, 1, 15, 10, 3))
    assert not task.is_due(dt.datetime(2026, 1, 15, 10, 59))


def test_every_minutes_next_due_and_hour_rollover():
    task = Schedule().command("tick").every_minutes(5)

    assert task.next_due(dt.datetime(2026, 1, 15, 10, 3)) == dt.datetime(2026, 1, 15, 10, 5)
    assert task.next_due(dt.datetime(2026, 1, 15, 10, 58)) == dt.datetime(2026, 1, 15, 11, 0)
    # a boundary moment has already fired — the next occurrence is strictly after
    assert task.next_due(dt.datetime(2026, 1, 15, 10, 5)) == dt.datetime(2026, 1, 15, 10, 10)


def test_every_minutes_rejects_nonpositive_intervals():
    schedule = Schedule()
    with pytest.raises(ValueError):
        schedule.command("tick").every_minutes(0)
    with pytest.raises(ValueError):
        schedule.command("tick").every_minutes(-5)


# --- daily / hourly ------------------------------------------------------------


def test_daily_at_next_due_same_day_and_across_midnight():
    task = Schedule().command("digest").daily_at("02:30")

    assert task.next_due(dt.datetime(2026, 1, 15, 1, 0)) == dt.datetime(2026, 1, 15, 2, 30)
    assert task.next_due(dt.datetime(2026, 1, 15, 3, 0)) == dt.datetime(2026, 1, 16, 2, 30)
    assert task.next_due(dt.datetime(2026, 1, 15, 2, 30)) == dt.datetime(2026, 1, 16, 2, 30)

    assert task.is_due(dt.datetime(2026, 1, 15, 2, 30))
    assert not task.is_due(dt.datetime(2026, 1, 15, 2, 31))
    assert not task.is_due(dt.datetime(2026, 1, 15, 14, 30))


def test_daily_next_due_from_mid_morning_is_tomorrow_midnight():
    task = Schedule().command("backups").daily()

    assert task.next_due(dt.datetime(2026, 1, 15, 9, 0)) == dt.datetime(2026, 1, 16, 0, 0)


def test_hourly_due_on_the_hour_only():
    task = Schedule().command("ping").hourly()

    assert task.is_due(dt.datetime(2026, 1, 15, 10, 0))
    assert not task.is_due(dt.datetime(2026, 1, 15, 10, 1))
    assert task.next_due(dt.datetime(2026, 1, 15, 10, 5)) == dt.datetime(2026, 1, 15, 11, 0)


def test_daily_at_rejects_out_of_range_times():
    schedule = Schedule()
    with pytest.raises(ValueError):
        schedule.command("bad").daily_at("25:00")
    with pytest.raises(ValueError):
        schedule.command("bad").daily_at("12:99")
    with pytest.raises(ValueError):
        schedule.command("bad").daily_at("0230")


# --- cron ----------------------------------------------------------------------


def test_cron_quarter_hour_expression_matches_only_quarters():
    task = Schedule().command("q").cron("*/15 * * * *")

    for minute in (0, 15, 30, 45):
        assert task.is_due(dt.datetime(2026, 1, 15, 10, minute)), minute
    for minute in (3, 7, 50, 59):
        assert not task.is_due(dt.datetime(2026, 1, 15, 10, minute)), minute


def test_cron_weekday_range_runs_weekday_mornings():
    task = Schedule().command("wd").cron("0 9 * * 1-5")

    assert task.is_due(dt.datetime(2026, 1, 5, 9, 0))  # Monday
    assert not task.is_due(dt.datetime(2026, 1, 3, 9, 0))  # Saturday
    assert not task.is_due(dt.datetime(2026, 1, 4, 9, 0))  # Sunday
    assert not task.is_due(dt.datetime(2026, 1, 5, 9, 1))  # Monday, but off the exact minute


def test_cron_day_of_month_list():
    task = Schedule().command("dom").cron("30 2 1,15 * *")

    assert task.is_due(dt.datetime(2026, 1, 1, 2, 30))
    assert task.is_due(dt.datetime(2026, 1, 15, 2, 30))
    assert not task.is_due(dt.datetime(2026, 1, 2, 2, 30))
    assert not task.is_due(dt.datetime(2026, 1, 15, 2, 31))


def test_cron_restricted_dom_and_dow_match_either():
    task = Schedule().command("either").cron("0 0 1 * 1")

    assert task.is_due(dt.datetime(2026, 1, 1, 0, 0))  # 1st of the month (Thursday)
    assert task.is_due(dt.datetime(2026, 1, 5, 0, 0))  # a Monday, not the 1st
    assert not task.is_due(dt.datetime(2026, 1, 6, 0, 0))  # neither the 1st nor a Monday


def test_cron_dow_seven_means_sunday():
    task = Schedule().command("sun").cron("0 6 * * 7")

    assert task.is_due(dt.datetime(2026, 1, 4, 6, 0))  # Sunday
    assert not task.is_due(dt.datetime(2026, 1, 3, 6, 0))  # Saturday


def test_cron_next_due_within_the_hour_and_to_tomorrow():
    quarter = Schedule().command("q").cron("*/15 * * * *")
    assert quarter.next_due(dt.datetime(2026, 1, 15, 10, 7)) == dt.datetime(2026, 1, 15, 10, 15)

    nightly = Schedule().command("n").cron("30 2 * * *")
    assert nightly.next_due(dt.datetime(2026, 1, 15, 3, 0)) == dt.datetime(2026, 1, 16, 2, 30)
    assert nightly.next_due(dt.datetime(2026, 1, 15, 2, 30)) == dt.datetime(2026, 1, 16, 2, 30)


@pytest.mark.parametrize(
    "expression",
    [
        "* * * *",  # four fields
        "* * * * * *",  # six fields
        "60 * * * *",  # minute out of range
        "* 24 * * *",  # hour out of range
        "0 0 0 * *",  # day-of-month zero
        "0 0 32 * *",  # day-of-month out of range
        "0 0 * 13 *",  # month out of range
        "0 0 * * 8",  # dow out of range
        "a * * * *",  # not numeric
        "*/0 * * * *",  # zero step
        "5-1 * * * *",  # inverted range
        "1,,2 * * * *",  # empty list item
        "",  # nothing at all
    ],
)
def test_cron_rejects_malformed_expressions(expression):
    with pytest.raises(ValueError):
        Schedule().command("bad").cron(expression)


# --- execution -----------------------------------------------------------------


def test_command_task_executes_through_injected_runner():
    schedule = Schedule()
    schedule.command("db:seed").every_minutes(5)
    calls: list[str] = []

    def fake_runner(command: str) -> int:
        calls.append(command)
        return 0

    results = asyncio.run(schedule.run_due(dt.datetime(2026, 1, 15, 10, 5), runner=fake_runner))

    assert calls == ["db:seed"]
    assert [r.ok for r in results] == [True]


def test_run_due_executes_only_due_tasks():
    schedule = Schedule()
    schedule.command("five").every_minutes(5)
    schedule.command("seven").every_minutes(7)
    calls: list[str] = []

    results = asyncio.run(
        schedule.run_due(dt.datetime(2026, 1, 15, 10, 10), runner=lambda cmd: calls.append(cmd))
    )

    assert calls == ["five"]  # 10 % 5 == 0, 10 % 7 != 0
    assert [r.task.name for r in results] == ["five"]


def test_run_due_awaits_async_and_calls_sync_callbacks():
    schedule = Schedule()
    ran: list[str] = []

    async def async_job() -> None:
        ran.append("async")

    def sync_job() -> str:
        ran.append("sync")
        return "sync-value"

    schedule.call(async_job).hourly()
    schedule.call(sync_job).hourly()

    results = asyncio.run(schedule.run_due(dt.datetime(2026, 1, 15, 10, 0)))

    assert ran == ["async", "sync"]
    values = {r.task.name: r.value for r in results}
    assert values["sync_job"] == "sync-value"
    assert values["async_job"] is None


def test_failing_task_is_recorded_and_does_not_stop_the_queue():
    schedule = Schedule()

    def boom() -> None:
        raise RuntimeError("boom")

    def fine() -> str:
        return "fine"

    schedule.call(boom).hourly()
    schedule.call(fine).hourly()

    results = asyncio.run(schedule.run_due(dt.datetime(2026, 1, 15, 10, 0)))

    assert [r.ok for r in results] == [False, True]
    assert isinstance(results[0].error, RuntimeError)
    assert results[1].value == "fine"


def test_default_runner_resolves_at_call_time(monkeypatch):
    recorded: list[str] = []

    def stub_runner(command: str) -> int:
        recorded.append(command)
        return 0

    monkeypatch.setattr("fastplace.schedule._subprocess_runner", stub_runner)

    schedule = Schedule()
    schedule.command("db:seed").every_minutes(5)
    asyncio.run(schedule.run_due(dt.datetime(2026, 1, 15, 10, 5)))

    assert recorded == ["db:seed"]


def test_find_locates_a_task_by_name():
    schedule = Schedule()
    schedule.command("db:seed").daily()

    assert schedule.find("db:seed") is not None
    assert schedule.find("nope") is None


# --- load_schedule ---------------------------------------------------------------


def _write_schedule(root, body: str) -> None:
    app_dir = root / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "__init__.py").write_text("")
    (app_dir / "schedule.py").write_text(body)


def _drop_schedule_modules() -> None:
    for name in ("app.schedule", "app"):
        sys.modules.pop(name, None)


def test_load_schedule_without_file_is_an_empty_schedule(tmp_path):
    schedule = load_schedule(tmp_path)

    assert schedule.tasks() == []


def test_load_schedule_imports_the_project_schedule_file(tmp_path):
    _write_schedule(
        tmp_path,
        "def schedule(s):\n"
        "    s.command('db:seed').daily_at('02:30')\n"
        "    s.command('ping').every_minutes(5)\n",
    )
    try:
        loaded = load_schedule(tmp_path)
    finally:
        _drop_schedule_modules()

    assert [t.name for t in loaded.tasks()] == ["db:seed", "ping"]


def test_load_schedule_raises_when_file_has_no_schedule_function(tmp_path):
    _write_schedule(tmp_path, "X = 1\n")
    try:
        with pytest.raises(ValueError, match="schedule"):
            load_schedule(tmp_path)
    finally:
        _drop_schedule_modules()


def test_load_schedule_raises_on_broken_project_module(tmp_path):
    _write_schedule(tmp_path, "import not_a_real_module_xyz\n")
    try:
        with pytest.raises(ModuleNotFoundError):
            load_schedule(tmp_path)
    finally:
        _drop_schedule_modules()


def test_load_schedule_accepts_time_only_and_iso_strings(tmp_path):
    """The `at` time parses both zero-padded and bare hour forms."""
    _write_schedule(
        tmp_path,
        "def schedule(s):\n    s.command('early').daily_at('2:30')\n",
    )
    try:
        loaded = load_schedule(tmp_path)
    finally:
        _drop_schedule_modules()

    assert loaded.tasks()[0].at == "02:30"
