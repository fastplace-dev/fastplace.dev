"""The scheduled-task registry — fluent expressions, cron matching, execution.

Projects define ``app/schedule.py`` with a ``def schedule(s: Schedule) -> None``
that registers tasks; ``fastplace schedule:list`` / ``schedule:run`` (and the
foreground worker) consume the registry. Pure stdlib on purpose — the 5-field
cron subset is matched by a small self-written parser, no new dependency
(zoneinfo and the cache/ maintenance integrations are lazy, function-local).

Scheduling is minute-granular by design: the worker ticks on minute
boundaries, so sub-minute expressions are deliberately out of scope. Runs
one deployment story: exactly one ``schedule:work`` (or one system cron
hitting ``schedule:run``) per app — tasks can additionally carry
``without_overlapping()`` for a cross-process mutex per task.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib
import importlib.util
import inspect
import logging
import shlex
import shutil
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: What a task runs: a zero-argument callable (sync or async) or a CLI
#: command string dispatched through a runner.
TaskCallback = Callable[[], Awaitable[Any] | Any]
CommandRunner = Callable[[str], Any]

_SCHEDULE_MODULE = "app.schedule"

logger = logging.getLogger("fastplace.schedule")

#: How far ``next_due`` searches for a cron match — past 4 years there is no
#: legal 5-field expression left unmet (leap days included).
_CRON_DAY_SEARCH_LIMIT = 366 * 4

#: Cache-store key prefix for a task's overlap mutex (plat-G1): the lock is
#: claimed per task name so a still-running firing (or a second ticker on
#: another instance sharing the cache) refuses to double-fire the task.
_SCHEDULE_LOCK_PREFIX = "schedule:lock:"

#: Overlap-mutex TTL when the task does not pin one — the crash safety net,
#: not the release path (a clean run releases in ``finally``).
_DEFAULT_OVERLAP_TTL = 3600

_WEEKDAY_NAMES = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


# ---------------------------------------------------------------------------
# cron parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CronField:
    """One parsed cron field: the allowed values plus whether it is a plain ``*``.

    ``unrestricted`` (a bare ``*``) drives the day-of-month/day-of-week OR
    rule: when both are restricted, cron matches *either*; otherwise both
    fields must match (POSIX cron semantics).
    """

    values: frozenset[int]
    unrestricted: bool


_CRON_BOUNDS = {
    "minute": (0, 59),
    "hour": (0, 23),
    "dom": (1, 31),
    "month": (1, 12),
    "dow": (0, 7),  # 0 and 7 both mean Sunday
}


def _parse_cron_field(name: str, text: str) -> _CronField:
    """Parse one cron field: ``*``, values, ranges, lists — each with ``/step``."""
    lo, hi = _CRON_BOUNDS[name]
    if text == "*":
        return _CronField(frozenset(range(lo, hi + 1)), unrestricted=True)

    values: set[int] = set()
    for item in text.split(","):
        if not item:
            raise ValueError(f"cron {name} field {text!r} has an empty list item")
        base, slash, step_text = item.partition("/")
        step = 1
        if slash:
            try:
                step = int(step_text)
            except ValueError:
                raise ValueError(f"cron {name} field {text!r} has a non-numeric step") from None
            if step < 1:
                raise ValueError(f"cron {name} field {text!r} has step {step}, must be >= 1")
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            start_text, _, end_text = base.partition("-")
            try:
                start, end = int(start_text), int(end_text)
            except ValueError:
                raise ValueError(f"cron {name} field {text!r} is not numeric") from None
        else:
            try:
                start = end = int(base)
            except ValueError:
                raise ValueError(f"cron {name} field {text!r} is not numeric") from None
        if start < lo or end > hi or start > end:
            raise ValueError(f"cron {name} field {text!r} is out of range ({lo}-{hi}) or inverted")
        values.update(range(start, end + 1, step))

    if name == "dow":
        # Both 0 and 7 name Sunday; normalize so matching compares one form.
        if 7 in values:
            values.discard(7)
            values.add(0)
    return _CronField(frozenset(values), unrestricted=False)


@dataclass(frozen=True)
class _CronSpec:
    minute: _CronField
    hour: _CronField
    dom: _CronField
    month: _CronField
    dow: _CronField

    @classmethod
    def parse(cls, expression: str) -> _CronSpec:
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError(
                f"cron expression {expression!r} must have 5 fields "
                "(minute hour day-of-month month day-of-week)"
            )
        minute, hour, dom, month, dow = parts
        return cls(
            minute=_parse_cron_field("minute", minute),
            hour=_parse_cron_field("hour", hour),
            dom=_parse_cron_field("dom", dom),
            month=_parse_cron_field("month", month),
            dow=_parse_cron_field("dow", dow),
        )

    def matches(self, moment: dt.datetime) -> bool:
        if (
            moment.minute not in self.minute.values
            or moment.hour not in self.hour.values
            or moment.month not in self.month.values
        ):
            return False
        cron_dow = (moment.weekday() + 1) % 7  # Monday=0 in Python, Sunday=0 in cron
        dom_ok = moment.day in self.dom.values
        dow_ok = cron_dow in self.dow.values
        if self.dom.unrestricted or self.dow.unrestricted:
            return dom_ok and dow_ok
        # Both restricted — the POSIX cron OR rule: either one satisfies.
        return dom_ok or dow_ok


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------


@dataclass
class TaskResult:
    """One executed task and its outcome; ``error`` is set unless ``ok``."""

    task: ScheduledTask
    ok: bool
    value: Any = None
    error: BaseException | None = None


def _subprocess_runner(command: str) -> int:
    """Default command runner — a real ``fastplace <command>`` subprocess.

    Resolved as a module global at call time so tests (and the foreground
    worker) can substitute their own runner via monkeypatching.
    """
    argv = shlex.split(command)
    fastplace = shutil.which("fastplace")
    if fastplace is not None:
        return subprocess.run([fastplace, *argv], check=False).returncode
    return subprocess.run([sys.executable, "-m", "fastplace", *argv], check=False).returncode


def _parse_time(at: str) -> tuple[int, int]:
    """``"HH:MM"`` (or bare ``"H:MM"``) to ``(hour, minute)``; ValueError otherwise."""
    parts = at.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"invalid time {at!r} — expected HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid time {at!r} — hour 00-23, minute 00-59")
    return hour, minute


def _parse_dow(day: str | int) -> int:
    """A weekday to cron's 0-6 (Sunday=0) — ``"mon"``/``"tues"`` or an int.

    Accepts the usual short spellings plus plain integers; 7 also means
    Sunday exactly as it does in cron itself.
    """
    if isinstance(day, int):
        if not 0 <= day <= 7:
            raise ValueError(f"invalid weekday {day!r} — expected 0-7 (Sunday=0)")
        return day % 7
    text = str(day).strip().lower()[:3]
    if text not in _WEEKDAY_NAMES:
        raise ValueError(f"invalid weekday {day!r} — expected sun/mon/tue/wed/thu/fri/sat or 0-6")
    return _WEEKDAY_NAMES[text]


class ScheduledTask:
    """One registered task: what it runs and when it is due.

    ``every`` names the frequency (``"5 minutes"``, ``"hour"``, ``"day"``, or
    ``"cron"``); ``at`` carries the ``HH:MM`` time for daily tasks; ``cron``
    keeps the raw 5-field expression. Exactly one of ``callback``/``command``
    is set — the registry's builders guarantee it.
    """

    def __init__(
        self,
        name: str,
        *,
        callback: TaskCallback | None = None,
        command: str | None = None,
        every: str = "cron",
        at: str | None = None,
        cron: str | None = None,
    ) -> None:
        if (callback is None) == (command is None):
            raise ValueError(f"task {name!r} needs exactly one of callback= or command=")
        self.name = name
        self.callback = callback
        self.command = command
        self.every = every
        self.at = at
        self.cron = cron
        # Hardening surface — all opt-in, all set through the fluent tail
        # (without_overlapping/even_in_maintenance/between/in_timezone).
        self._overlap_ttl: int | None = None
        self._maintenance_exempt = False
        self._window: tuple[tuple[int, int], tuple[int, int]] | None = None
        self._tz: dt.tzinfo | None = None
        self._spec = _CronSpec.parse(cron) if cron is not None else None
        # Frequency must be internally consistent — a mismatch would otherwise
        # surface as a ZeroDivisionError in is_due on the first tick.
        if every.endswith(" minutes"):
            try:
                self._minutes_n = int(every.removesuffix(" minutes"))
            except ValueError:
                raise ValueError(f"invalid frequency {every!r}") from None
            if self._minutes_n < 1:
                raise ValueError(f"invalid frequency {every!r}")
        else:
            self._minutes_n = 0
            if every not in ("hour", "day", "cron"):
                raise ValueError(f"unknown frequency {every!r}")
            if every == "day" and at is None:
                raise ValueError("daily tasks need an at= time")
            if every == "cron" and cron is None:
                raise ValueError("cron tasks need a cron= expression")
        self._at_hm = _parse_time(at) if at is not None else None

    # -- fluent hardening tail (post-registration mutators) -------------------

    def without_overlapping(self, ttl: int = _DEFAULT_OVERLAP_TTL) -> ScheduledTask:
        """Carry a cross-process mutex: one firing of this task at a time.

        ``run_due`` claims ``schedule:lock:<name>`` on the cache store before
        executing and releases it in a ``finally``; a concurrent ticker (a
        second ``schedule:work`` instance, or a ``schedule:run`` landing
        mid-run) finds the claim held and skips the firing. ``ttl`` is only
        the crash safety net — a worker that dies mid-task never reaches the
        release, and the mutex must not stay latched forever afterwards.
        Cross-instance locking needs a shared cache driver (redis); an
        unreachable cache fails OPEN (the task runs) — availability over
        duplicate-suppression, mirroring the queue restart sentinel.
        """
        if ttl < 1:
            raise ValueError(f"without_overlapping(ttl={ttl!r}) — the ttl must be >= 1 second")
        self._overlap_ttl = ttl
        return self

    def even_in_maintenance(self) -> ScheduledTask:
        """Keep firing while the application is in maintenance mode.

        ``run_due`` skips every task while ``fastplace down`` state exists;
        this is the escape hatch for the tasks maintenance depends on
        (cleanup, the maintenance window's own bookkeeping, …).
        """
        self._maintenance_exempt = True
        return self

    def between(self, start: str, end: str) -> ScheduledTask:
        """Restrict firing to a wall-clock window, ``HH:MM`` to ``HH:MM``.

        Inclusive on both ends; ``start > end`` reads as an overnight window
        (22:00–06:00 wraps midnight). Reads in the task's
        :meth:`in_timezone` zone when one is set. A filter, not a schedule:
        ``next_due`` still reports the next frequency slot — this window may
        suppress it.
        """
        self._window = (_parse_time(start), _parse_time(end))
        return self

    def in_timezone(self, zone: str | dt.tzinfo) -> ScheduledTask:
        """Evaluate this task's times in a named IANA zone.

        ``daily_at("02:30")`` in ``Asia/Dhaka`` fires on Dhaka's wall clock,
        DST included (zoneinfo). An aware ``now`` is converted; a naive one
        is read as the server's local wall time and then converted. Daily
        arithmetic runs on the zone's wall clock, so DST transitions keep
        the fire time at 02:30 local rather than a fixed UTC instant.
        """
        if isinstance(zone, str):
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                zone = ZoneInfo(zone)
            except ZoneInfoNotFoundError:
                raise ValueError(f"unknown timezone {zone!r}") from None
        self._tz = zone
        return self

    # -- schedule ------------------------------------------------------------

    @property
    def expression(self) -> str:
        """Human-readable form for listings; cron keeps its raw expression."""
        if self.cron is not None:
            return self.cron
        if self.every == "hour":
            return "hourly"
        if self.every == "day":
            return "daily" if self.at == "00:00" else f"daily at {self.at}"
        return f"every {self._minutes_n} minute" if self._minutes_n == 1 else f"every {self.every}"

    def _wall_moment(self, moment: dt.datetime) -> dt.datetime:
        """``moment`` as the time-of-day the task reads: naive stays naive
        (no zone declared, or the caller's wall clock is already the truth);
        an aware moment converts into the task's zone."""
        if self._tz is None:
            return moment
        if moment.tzinfo is None:
            # A naive clock is the server's wall time — anchor it to the
            # server's zone, then convert (the aware path below is exact).
            local = dt.datetime.now().astimezone().tzinfo
            moment = moment.replace(tzinfo=local)
        return moment.astimezone(self._tz)

    def _in_window(self, wall: dt.datetime) -> bool:
        assert self._window is not None  # callers check first
        start, end = self._window
        now_hm = (wall.hour, wall.minute)
        if start <= end:
            return start <= now_hm <= end
        return now_hm >= start or now_hm <= end  # overnight wrap

    def is_due(self, now: dt.datetime | None = None) -> bool:
        """Whether the minute containing ``now`` is a fire time.

        Minute-granular on purpose: the worker ticks on minute boundaries and
        ``schedule:run`` fires everything whose slot is the current minute.
        Evaluation happens on the task's ``in_timezone`` wall clock, and a
        ``between()`` window suppresses firings outside it.
        """
        moment = dt.datetime.now() if now is None else now
        wall = self._wall_moment(moment)
        if self._window is not None and not self._in_window(wall):
            return False
        if self._spec is not None:
            return self._spec.matches(wall)
        if self.every == "hour":
            return wall.minute == 0
        if self.every == "day":
            return (wall.hour, wall.minute) == self._at_hm
        return wall.minute % self._minutes_n == 0

    def next_due(self, now: dt.datetime | None = None) -> dt.datetime:
        """The next fire time strictly after ``now``.

        A boundary moment itself has already fired, so ``next_due`` at exactly
        10:05 for a 5-minute task is 10:10 — ``is_due`` owns "is this minute
        live", ``next_due`` owns "when is the next one". The search runs on
        the task's zone wall clock; a timezone task returns an aware datetime
        in that zone. A ``between()`` window is a filter on firing, not part
        of the schedule — the returned slot may still be suppressed by it.
        """
        moment = dt.datetime.now() if now is None else now
        wall = self._wall_moment(moment)
        if wall.tzinfo is not None:
            # Day-stepping arithmetic on aware datetimes is absolute time —
            # across a DST boundary a "day" is 23/25 hours and the wall clock
            # drifts. Compute on naive wall time, re-attach the zone at the
            # end (the intent is "this wall time, in this zone").
            wall = wall.replace(tzinfo=None)
        candidate = self._next_naive(wall)
        if self._tz is not None:
            candidate = candidate.replace(tzinfo=self._tz)
        return candidate

    def _next_naive(self, wall: dt.datetime) -> dt.datetime:
        if self._spec is not None:
            return self._next_cron(self._spec, wall)
        if self.every == "hour":
            return wall.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        if self.every == "day":
            at_hm = self._at_hm
            if at_hm is None:  # pragma: no cover - builders always pass `at` for daily tasks
                raise ValueError("daily task registered without an at time")
            candidate = wall.replace(hour=at_hm[0], minute=at_hm[1], second=0, microsecond=0)
            if candidate <= wall:
                candidate += dt.timedelta(days=1)
            return candidate
        step = self._minutes_n
        candidate = wall.replace(second=0, microsecond=0)
        while True:
            candidate += dt.timedelta(minutes=1)
            if candidate.minute % step == 0:
                return candidate

    def _next_cron(self, spec: _CronSpec, moment: dt.datetime) -> dt.datetime:
        hour_minute = (moment.hour, moment.minute)
        day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        for _ in range(_CRON_DAY_SEARCH_LIMIT):
            if not self._day_matches(spec, day):
                day += dt.timedelta(days=1)
                continue
            today = day.date() == moment.date()
            for hour in sorted(spec.hour.values):
                for minute in sorted(spec.minute.values):
                    if not today or (hour, minute) > hour_minute:
                        return day.replace(hour=hour, minute=minute)
            day += dt.timedelta(days=1)
        raise ValueError(f"cron expression {self.cron!r} never matches a real date")

    @staticmethod
    def _day_matches(spec: _CronSpec, day: dt.datetime) -> bool:
        if day.month not in spec.month.values:
            return False
        cron_dow = (day.weekday() + 1) % 7
        dom_ok = day.day in spec.dom.values
        dow_ok = cron_dow in spec.dow.values
        if spec.dom.unrestricted or spec.dow.unrestricted:
            return dom_ok and dow_ok
        return dom_ok or dow_ok

    # -- execution -----------------------------------------------------------

    async def execute(self, runner: CommandRunner | None = None) -> Any:
        """Run the task: await async callbacks, call sync ones, dispatch commands.

        Command tasks go through ``runner`` — the module's subprocess runner
        unless one is injected (tests, the foreground worker).
        """
        if self.callback is not None:
            result = self.callback()
            if inspect.isawaitable(result):
                result = await result
            return result
        assert self.command is not None  # exactly one is set, guaranteed at init
        resolved = runner if runner is not None else _subprocess_runner
        return resolved(self.command)


class _TaskBuilder:
    """The fluent tail of ``Schedule.command()``/``Schedule.call()``.

    Every method terminates a registration: it finalizes the task with the
    chosen frequency, adds it to the schedule, and returns it.
    """

    def __init__(
        self,
        schedule: Schedule,
        *,
        callback: TaskCallback | None = None,
        command: str | None = None,
        name: str | None = None,
    ) -> None:
        self._schedule = schedule
        self._callback = callback
        self._command = command
        self._name = (
            name
            if name is not None
            else (command if command is not None else getattr(callback, "__name__", "task"))
        )

    def every_minutes(self, n: int) -> ScheduledTask:
        if not isinstance(n, int) or n < 1:
            raise ValueError(f"every_minutes({n!r}) — the interval must be a positive integer")
        return self._register(every=f"{n} minutes")

    def hourly(self) -> ScheduledTask:
        return self._register(every="hour")

    def daily(self) -> ScheduledTask:
        return self._register(every="day", at="00:00")

    def daily_at(self, at: str) -> ScheduledTask:
        hm = _parse_time(at)  # reject bad times at registration, not mid-run
        return self._register(every="day", at="{:02d}:{:02d}".format(*hm))

    def cron(self, expression: str) -> ScheduledTask:
        _CronSpec.parse(expression)  # reject malformed expressions at registration
        return self._register(every="cron", cron=expression)

    # -- calendar frequencies (translated to the 5-field cron subset) ---------

    def weekly_on(self, day: str | int, at: str) -> ScheduledTask:
        """Fire once a week on the named day (``"mon"``…``"sat"`` or 0-7,
        Sunday=0) at ``HH:MM`` — e.g. ``weekly_on("mon", "09:30")``."""
        dow = _parse_dow(day)
        h, m = _parse_time(at)
        return self.cron(f"{m} {h} * * {dow}")

    def weekly(self) -> ScheduledTask:
        """Fire every Sunday at midnight — pair with ``weekly_on`` for any
        other day or time."""
        return self.cron("0 0 * * 0")

    def monthly_on(self, day: int, at: str) -> ScheduledTask:
        """Fire once a month on ``day`` (1-31) at ``HH:MM``. Months without
        that day (Feb 30) simply skip — cron day-of-month semantics."""
        if not isinstance(day, int) or not 1 <= day <= 31:
            raise ValueError(f"monthly_on({day!r}) — the day must be an integer 1-31")
        h, m = _parse_time(at)
        return self.cron(f"{m} {h} {day} * *")

    def monthly(self) -> ScheduledTask:
        """Fire on the first of every month at midnight."""
        return self.cron("0 0 1 * *")

    def weekdays(self, at: str) -> ScheduledTask:
        """Fire Monday through Friday at ``HH:MM`` — the commute schedule."""
        h, m = _parse_time(at)
        return self.cron(f"{m} {h} * * 1-5")

    def yearly(self) -> ScheduledTask:
        """Fire once a year on January 1st at midnight."""
        return self.cron("0 0 1 1 *")

    def _register(
        self,
        *,
        every: str,
        at: str | None = None,
        cron: str | None = None,
    ) -> ScheduledTask:
        task = ScheduledTask(
            self._name,
            callback=self._callback,
            command=self._command,
            every=every,
            at=at,
            cron=cron,
        )
        self._schedule._tasks.append(task)
        return task


class Schedule:
    """The registry tasks attach to; ``app/schedule.py`` receives one instance."""

    def __init__(self) -> None:
        self._tasks: list[ScheduledTask] = []

    # -- registration --------------------------------------------------------

    def command(self, name: str) -> _TaskBuilder:
        """Register a CLI command task, e.g. ``s.command("db:seed").daily()``."""
        return _TaskBuilder(self, command=name)

    def call(self, callback: TaskCallback, name: str | None = None) -> _TaskBuilder:
        """Register a callable task (sync or async, zero-argument)."""
        return _TaskBuilder(self, callback=callback, name=name)

    # -- inspection ----------------------------------------------------------

    def tasks(self) -> list[ScheduledTask]:
        """The registered tasks in definition order."""
        return list(self._tasks)

    def find(self, name: str) -> ScheduledTask | None:
        """The task registered under ``name``, if any."""
        return next((task for task in self._tasks if task.name == name), None)

    # -- execution -----------------------------------------------------------

    async def run_due(
        self,
        now: dt.datetime | None = None,
        runner: CommandRunner | None = None,
        root: Path | None = None,
    ) -> list[TaskResult]:
        """Execute every task due at ``now`` once, sequentially.

        Two guards wrap the plain due-and-execute loop, both opt-in from the
        task's fluent tail: while the project at ``root`` (cwd by default —
        the CLI commands already run from the project root) is in maintenance
        mode every task skips except those marked
        ``even_in_maintenance()``; and a ``without_overlapping()`` task must
        claim its mutex first — a claim already held means another firing is
        in flight and this one skips (released in ``finally``, TTL as the
        crash safety net). A failing task is captured as a failed
        ``TaskResult`` — one bad task never stops the ones behind it.
        """
        from fastplace.http.maintenance import is_down

        moment = dt.datetime.now() if now is None else now
        # ``is_down`` returns the state dict while down — {} on a corrupt
        # file, still DOWN. Only ``None`` means up; the dict itself is falsy.
        down = is_down(root if root is not None else Path.cwd()) is not None
        results: list[TaskResult] = []
        for task in self._tasks:
            if down and not task._maintenance_exempt:
                continue
            if not task.is_due(moment):
                continue
            if task._overlap_ttl is not None and not await _claim_task_lock(
                task.name, task._overlap_ttl
            ):
                continue
            try:
                value = await task.execute(runner)
                results.append(TaskResult(task, ok=True, value=value))
            except Exception as exc:  # recorded per task — one failure never stops the rest
                results.append(TaskResult(task, ok=False, error=exc))
            finally:
                if task._overlap_ttl is not None:
                    await _release_task_lock(task.name)
        return results


# ---------------------------------------------------------------------------
# overlap mutex (plat-G1) — cache-store claim, one firing of a task at a time
# ---------------------------------------------------------------------------


async def _claim_task_lock(name: str, ttl: int) -> bool:
    """Claim ``schedule:lock:<name>``; ``False`` when another firing holds it.

    The atomic increment idiom documented on the queue restart sentinel:
    ``increment`` returns 1 only to the first claimer; the ttl is the crash
    safety net (a worker dying mid-task never releases). An unreachable
    cache fails OPEN — the task runs — availability over
    duplicate-suppression, mirroring the sentinel's posture.
    """
    from fastplace.cache import cache

    try:
        return await cache().increment(f"{_SCHEDULE_LOCK_PREFIX}{name}", ttl=ttl) == 1
    except Exception:  # noqa: BLE001 — a broken cache must not stop the schedule
        logger.warning("overlap lock unreachable for task %r — running unlocked", name)
        return True


async def _release_task_lock(name: str) -> None:
    """Drop the overlap mutex — best-effort; the ttl recovers a failed drop."""
    from fastplace.cache import cache

    try:
        await cache().forget(f"{_SCHEDULE_LOCK_PREFIX}{name}")
    except Exception:  # noqa: BLE001 — the ttl expires the stale claim
        logger.warning("overlap lock release failed for task %r — ttl will expire it", name)


async def run_worker(
    schedule: Schedule,
    *,
    clock: Callable[[], dt.datetime] | None = None,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    stop_after: int | None = None,
    on_tick: Callable[[list[TaskResult]], Any] | None = None,
) -> None:
    """Tick every minute and run whatever is due — the foreground worker loop.

    Each iteration reads ``clock`` (real wall time by default), fires the
    tasks due that minute through ``Schedule.run_due`` — a failing task is
    isolated there and never stops the loop — hands the tick's results to
    ``on_tick``, then waits via ``sleep_fn`` exactly to the next minute
    boundary (``asyncio.sleep`` by default). ``stop_after`` caps the tick
    count: a hook for tests and embedders, never a CLI option. Cancellation
    and KeyboardInterrupt during the wait propagate, so ``asyncio.run`` shuts
    the loop down cleanly with no traceback.
    """
    now_fn = clock if clock is not None else dt.datetime.now
    sleep = sleep_fn if sleep_fn is not None else asyncio.sleep
    ticks = 0
    while stop_after is None or ticks < stop_after:
        now = now_fn()
        results = await schedule.run_due(now)
        if on_tick is not None:
            on_tick(results)
        ticks += 1
        if stop_after is not None and ticks >= stop_after:
            return
        # Sleep to the next minute boundary — the worker's whole cadence.
        # ``max`` guards a slow tick that already ran past the boundary.
        next_minute = (now + dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
        await sleep(max(0.0, (next_minute - now).total_seconds()))


# ---------------------------------------------------------------------------
# project loading
# ---------------------------------------------------------------------------


def _import_by_path(schedule_file: Path) -> Any:
    """Load ``schedule.py`` under its canonical dotted name, straight from disk.

    Used when the dotted import cannot see the project's ``app`` package — a
    foreign regular ``app`` package elsewhere on sys.path shadows the
    project's (PEP 420 namespace) ``app`` no matter where the root sits.
    """
    spec = importlib.util.spec_from_file_location(_SCHEDULE_MODULE, schedule_file)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load the schedule from {schedule_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_SCHEDULE_MODULE] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # A half-registered broken module must not poison later imports.
        sys.modules.pop(_SCHEDULE_MODULE, None)
        raise
    return module


def load_schedule(root: str | Path) -> Schedule:
    """Load the project's ``app/schedule.py`` into a fresh registry.

    An absent file is a fresh project's empty schedule, not an error. A
    present file must define ``def schedule(s: Schedule) -> None``; broken
    project code raises — fail loud at load, not on the first tick.
    """
    root_path = Path(root)
    schedule_file = root_path / "app" / "schedule.py"
    registry = Schedule()
    if not schedule_file.is_file():
        return registry

    root_str = str(root_path)
    # Scope the path entry to this call (the import_gates precedent: leaving
    # it behind would hijack the next `import app` elsewhere in the process).
    inserted = root_str not in sys.path
    if inserted:
        sys.path.insert(0, root_str)
    try:
        try:
            module = importlib.import_module(_SCHEDULE_MODULE)
        except ModuleNotFoundError as exc:
            if exc.name not in ("app", "app.schedule"):
                raise  # broken schedule module — fail loud
            # The project's app package is unreachable by name (shadowed);
            # load the file directly so the registry still fills. Content
            # errors inside it keep raising.
            module = _import_by_path(schedule_file)
    finally:
        if inserted:
            sys.path.remove(root_str)

    configure = getattr(module, "schedule", None)
    if not callable(configure):
        raise ValueError(
            "app/schedule.py defines no schedule(s) function — "
            "expected `def schedule(s: Schedule) -> None`"
        )
    configure(registry)
    return registry
