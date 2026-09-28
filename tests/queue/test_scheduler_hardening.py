"""Scheduler hardening — overlap locks (plat-G1), maintenance skip (plat-G5),
timezone + frequency surface (plat-G3).

``run_due`` gains three guards, all opt-in from the fluent task tail:

* ``without_overlapping()`` — a cache-store mutex per task name, so a task
  still running (or a second ticker on another instance) cannot double-fire.
* ``even_in_maintenance()`` — the escape hatch; every other task is skipped
  while ``fastplace down`` state exists.
* ``in_timezone(...)`` / ``.between(...)`` — wall-clock evaluation in a named
  zone and a time-window filter, plus weekly/monthly/weekdays/yearly builders
  expressed through the cron translator.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from zoneinfo import ZoneInfo

import pytest

from fastplace.cache import reset_cache
from fastplace.schedule import Schedule

UTC = ZoneInfo("UTC")
DHAKA = ZoneInfo("Asia/Dhaka")


@pytest.fixture(autouse=True)
def _fresh_state():
    """Cache lock keys and the queue-adjacent singletons never leak."""
    reset_cache()
    yield
    reset_cache()


def _write_maintenance(root, state=None) -> None:
    """The exact artifact `fastplace down` leaves behind."""
    path = root / "storage" / "framework" / "maintenance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state or {}))


# ---------------------------------------------------------------------------
# plat-G1 — without_overlapping: one run of a task at a time
# ---------------------------------------------------------------------------


async def test_without_overlapping_skips_a_concurrent_second_run():
    """A task that is still executing holds its lock — a second ticker
    firing the same task in that window must skip, not double-fire."""
    ran: list[int] = []
    schedule = Schedule()

    async def work() -> None:
        ran.append(1)
        await asyncio.sleep(0.05)  # in-flight while the second ticker lands

    schedule.call(work, name="overlap.probe").every_minutes(1).without_overlapping()

    now = dt.datetime(2026, 9, 28, 7, 30)
    first, second = await asyncio.gather(
        schedule.run_due(now),
        schedule.run_due(now),  # two schedule:work ticks
    )

    assert len(ran) == 1  # the lock refused the duplicate firing
    assert len(first) + len(second) == 1  # exactly one tick executed it


async def test_without_overlapping_releases_the_lock_on_completion():
    """The mutex spans one execution — the NEXT due firing runs again."""
    ran: list[int] = []
    schedule = Schedule()

    def work() -> None:
        ran.append(1)

    schedule.call(work, name="overlap.after").every_minutes(1).without_overlapping()

    await schedule.run_due(dt.datetime(2026, 9, 28, 7, 30))
    await schedule.run_due(dt.datetime(2026, 9, 28, 7, 31))

    assert len(ran) == 2  # released after the first run — not latched forever


async def test_without_overlapping_holds_through_the_task_body():
    """Re-entrancy from inside the handler (a task that itself triggers a
    run) finds the lock held — the guard spans the execution, not the tick."""
    ran: list[int] = []
    schedule = Schedule()

    async def recursive() -> None:
        ran.append(1)
        if len(ran) == 1:
            await schedule.run_due(dt.datetime(2026, 9, 28, 7, 30))

    schedule.call(recursive, name="overlap.recurse").every_minutes(1).without_overlapping()

    await schedule.run_due(dt.datetime(2026, 9, 28, 7, 30))
    assert len(ran) == 1  # the nested run skipped the still-running task


async def test_without_overlapping_two_schedules_share_one_lock():
    """Two worker processes = two Schedule objects over one shared cache —
    the claim is store-wide, so the second instance's run skips."""
    ran: list[int] = []
    first = Schedule()
    second = Schedule()

    async def work() -> None:
        ran.append(1)
        await asyncio.sleep(0.05)

    for registry in (first, second):
        registry.call(work, name="overlap.shared").every_minutes(1).without_overlapping()

    now = dt.datetime(2026, 9, 28, 7, 30)
    results = await asyncio.gather(first.run_due(now), second.run_due(now))

    assert len(ran) == 1
    assert sum(len(tick) for tick in results) == 1


async def test_tasks_without_the_flag_still_overlap_freely():
    """The default behavior is untouched — no flag, no lock, no skip."""
    ran: list[int] = []
    schedule = Schedule()

    async def work() -> None:
        ran.append(1)
        await asyncio.sleep(0.05)

    schedule.call(work, name="overlap.free").every_minutes(1)

    now = dt.datetime(2026, 9, 28, 7, 30)
    await asyncio.gather(schedule.run_due(now), schedule.run_due(now))
    assert len(ran) == 2


# ---------------------------------------------------------------------------
# plat-G5 — maintenance mode: tasks skip while the app is down
# ---------------------------------------------------------------------------


async def test_run_due_skips_tasks_while_maintenance_is_active(tmp_path):
    ran: list[int] = []
    schedule = Schedule()

    def work() -> None:
        ran.append(1)

    schedule.call(work, name="maint.skip").every_minutes(1)
    _write_maintenance(tmp_path)

    results = await schedule.run_due(dt.datetime(2026, 9, 28, 7, 30), root=tmp_path)

    assert ran == []
    assert results == []


async def test_even_in_maintenance_tasks_run_while_down(tmp_path):
    ran: list[int] = []
    schedule = Schedule()

    def work() -> None:
        ran.append(1)

    schedule.call(work, name="maint.escape").every_minutes(1).even_in_maintenance()
    _write_maintenance(tmp_path)

    results = await schedule.run_due(dt.datetime(2026, 9, 28, 7, 30), root=tmp_path)

    assert len(ran) == 1
    assert len(results) == 1 and results[0].ok


async def test_maintenance_skip_lifts_when_the_app_comes_back_up(tmp_path):
    ran: list[int] = []
    schedule = Schedule()

    def work() -> None:
        ran.append(1)

    schedule.call(work, name="maint.resume").every_minutes(1)

    _write_maintenance(tmp_path)
    await schedule.run_due(dt.datetime(2026, 9, 28, 7, 30), root=tmp_path)
    assert ran == []

    (tmp_path / "storage" / "framework" / "maintenance.json").unlink()
    await schedule.run_due(dt.datetime(2026, 9, 28, 7, 31), root=tmp_path)
    assert len(ran) == 1


async def test_run_due_defaults_to_the_cwd_root(tmp_path, monkeypatch):
    """No root= passed: the project around the process decides — the CLI
    commands run from the project root, so cwd is the honest default."""
    ran: list[int] = []
    schedule = Schedule()

    def work() -> None:
        ran.append(1)

    schedule.call(work, name="maint.cwd").every_minutes(1)
    _write_maintenance(tmp_path)
    monkeypatch.chdir(tmp_path)

    await schedule.run_due(dt.datetime(2026, 9, 28, 7, 30))
    assert ran == []  # down at cwd → skipped


# ---------------------------------------------------------------------------
# plat-G3 — weekly/monthly/weekdays/yearly builders (cron translations)
# ---------------------------------------------------------------------------


def test_weekly_on_fires_on_the_chosen_weekday_and_time():
    schedule = Schedule()
    task = schedule.command("reports:weekly").weekly_on("mon", "09:30")

    assert task.is_due(dt.datetime(2026, 9, 28, 9, 30))  # a Monday
    assert not task.is_due(dt.datetime(2026, 9, 29, 9, 30))  # Tuesday
    assert not task.is_due(dt.datetime(2026, 9, 28, 9, 31))
    # The next fire after Monday 09:31 is the NEXT Monday at 09:30.
    assert task.next_due(dt.datetime(2026, 9, 28, 9, 31)) == dt.datetime(2026, 10, 5, 9, 30)


def test_weekday_names_and_integers_both_register():
    schedule = Schedule()
    by_name = schedule.command("a").weekly_on("fri", "10:00")
    by_int = schedule.command("b").weekly_on(5, "10:00")
    friday = dt.datetime(2026, 10, 2, 10, 0)

    assert by_name.is_due(friday) and by_int.is_due(friday)


def test_monthly_on_fires_on_the_chosen_day():
    schedule = Schedule()
    task = schedule.command("billing:close").monthly_on(1, "00:00")

    assert task.is_due(dt.datetime(2026, 10, 1, 0, 0))
    assert not task.is_due(dt.datetime(2026, 10, 2, 0, 0))
    assert task.next_due(dt.datetime(2026, 9, 28, 12, 0)) == dt.datetime(2026, 10, 1, 0, 0)


def test_monthly_defaults_to_the_first_at_midnight():
    task = Schedule().command("m").monthly()
    assert task.is_due(dt.datetime(2026, 10, 1, 0, 0))
    assert not task.is_due(dt.datetime(2026, 10, 1, 0, 1))


def test_weekdays_runs_monday_through_friday_only():
    task = Schedule().command("w").weekdays("08:00")
    assert task.is_due(dt.datetime(2026, 9, 28, 8, 0))  # Monday
    assert task.is_due(dt.datetime(2026, 10, 2, 8, 0))  # Friday
    assert not task.is_due(dt.datetime(2026, 10, 3, 8, 0))  # Saturday
    assert not task.is_due(dt.datetime(2026, 10, 4, 8, 0))  # Sunday


def test_yearly_fires_once_a_year():
    task = Schedule().command("y").yearly()
    assert task.is_due(dt.datetime(2027, 1, 1, 0, 0))
    assert not task.is_due(dt.datetime(2027, 1, 1, 0, 1))
    assert not task.is_due(dt.datetime(2027, 1, 2, 0, 0))


def test_between_filters_firing_to_the_window():
    schedule = Schedule()
    task = schedule.command("market:poll").hourly().between("09:00", "17:00")

    assert task.is_due(dt.datetime(2026, 9, 28, 10, 0))
    assert task.is_due(dt.datetime(2026, 9, 28, 9, 0))  # window is inclusive
    assert not task.is_due(dt.datetime(2026, 9, 28, 8, 0))
    assert not task.is_due(dt.datetime(2026, 9, 28, 18, 0))


def test_between_accepts_an_overnight_window():
    """22:00–06:00 wraps midnight — a window, not an interval."""
    task = Schedule().command("night:watch").hourly().between("22:00", "06:00")

    assert task.is_due(dt.datetime(2026, 9, 28, 23, 0))
    assert task.is_due(dt.datetime(2026, 9, 28, 3, 0))
    assert not task.is_due(dt.datetime(2026, 9, 28, 12, 0))


def test_between_rejects_malformed_times_at_registration():
    with pytest.raises(ValueError, match="invalid time"):
        Schedule().command("x").hourly().between("9am", "17:00")


# ---------------------------------------------------------------------------
# plat-G3 — in_timezone: wall-clock evaluation in a named zone
# ---------------------------------------------------------------------------


def test_in_timezone_evaluates_the_schedule_in_that_zone():
    """02:30 Dhaka is 20:30 UTC the previous day — the task fires on Dhaka
    wall time, not the server's."""
    task = Schedule().command("tz:dhaka").daily_at("02:30").in_timezone("Asia/Dhaka")

    assert task.is_due(dt.datetime(2026, 9, 28, 20, 30, tzinfo=UTC))
    assert not task.is_due(dt.datetime(2026, 9, 28, 2, 30, tzinfo=UTC))


def test_in_timezone_next_due_returns_aware_wall_time():
    task = Schedule().command("tz:next").daily_at("02:30").in_timezone("Asia/Dhaka")

    due = task.next_due(dt.datetime(2026, 9, 28, 20, 0, tzinfo=UTC))
    assert due == dt.datetime(2026, 9, 28, 20, 30, tzinfo=UTC)  # 02:30 Dhaka wall


def test_in_timezone_rejects_unknown_zones_at_registration():
    with pytest.raises(Exception, match="Mars/Olympus"):
        Schedule().command("x").daily().in_timezone("Mars/Olympus")


def test_between_applies_in_the_task_timezone_too():
    """The window reads in the task's zone — 09:00–17:00 Dhaka, i.e.
    03:00–11:00 UTC."""
    task = (
        Schedule().command("tz:window").hourly().between("09:00", "17:00").in_timezone("Asia/Dhaka")
    )

    assert task.is_due(dt.datetime(2026, 9, 28, 4, 0, tzinfo=UTC))  # 10:00 Dhaka
    assert not task.is_due(dt.datetime(2026, 9, 28, 13, 0, tzinfo=UTC))  # 19:00 Dhaka


async def test_without_overlapping_and_maintenance_compose_with_the_surface(tmp_path):
    """The fluent tail composes: a locked, timezone-aware task with the
    maintenance escape hatch skips while down and runs cleanly after."""
    ran: list[int] = []
    schedule = Schedule()

    def work() -> None:
        ran.append(1)

    schedule.call(work, name="compose.all").hourly().in_timezone("UTC").without_overlapping()

    _write_maintenance(tmp_path)
    await schedule.run_due(dt.datetime(2026, 9, 28, 7, 0), root=tmp_path)
    assert ran == []

    (tmp_path / "storage" / "framework" / "maintenance.json").unlink()
    await schedule.run_due(dt.datetime(2026, 9, 28, 7, 0), root=tmp_path)
    assert len(ran) == 1
