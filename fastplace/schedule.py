"""The scheduled-task registry — fluent expressions, cron matching, execution.

Projects define ``app/schedule.py`` with a ``def schedule(s: Schedule) -> None``
that registers tasks; ``fastplace schedule:list`` / ``schedule:run`` (and the
foreground worker) consume the registry. Pure stdlib on purpose — the 5-field
cron subset is matched by a small self-written parser, no new dependency.
"""

from __future__ import annotations

import datetime as dt
import importlib
import importlib.util
import inspect
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

#: How far ``next_due`` searches for a cron match — past 4 years there is no
#: legal 5-field expression left unmet (leap days included).
_CRON_DAY_SEARCH_LIMIT = 366 * 4


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

    def is_due(self, now: dt.datetime | None = None) -> bool:
        """Whether the minute containing ``now`` is a fire time.

        Minute-granular on purpose: the worker ticks on minute boundaries and
        ``schedule:run`` fires everything whose slot is the current minute.
        """
        moment = dt.datetime.now() if now is None else now
        if self._spec is not None:
            return self._spec.matches(moment)
        if self.every == "hour":
            return moment.minute == 0
        if self.every == "day":
            return (moment.hour, moment.minute) == self._at_hm
        return moment.minute % self._minutes_n == 0

    def next_due(self, now: dt.datetime | None = None) -> dt.datetime:
        """The next fire time strictly after ``now``.

        A boundary moment itself has already fired, so ``next_due`` at exactly
        10:05 for a 5-minute task is 10:10 — ``is_due`` owns "is this minute
        live", ``next_due`` owns "when is the next one".
        """
        moment = dt.datetime.now() if now is None else now
        if self._spec is not None:
            return self._next_cron(self._spec, moment)
        if self.every == "hour":
            candidate = moment.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
            return candidate
        if self.every == "day":
            at_hm = self._at_hm
            if at_hm is None:  # pragma: no cover - builders always pass `at` for daily tasks
                raise ValueError("daily task registered without an at time")
            candidate = moment.replace(hour=at_hm[0], minute=at_hm[1], second=0, microsecond=0)
            if candidate <= moment:
                candidate += dt.timedelta(days=1)
            return candidate
        step = self._minutes_n
        candidate = moment.replace(second=0, microsecond=0)
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
    ) -> list[TaskResult]:
        """Execute every task due at ``now`` once, sequentially.

        A failing task is captured as a failed ``TaskResult`` — one bad task
        never stops the ones behind it.
        """
        moment = dt.datetime.now() if now is None else now
        results: list[TaskResult] = []
        for task in self._tasks:
            if not task.is_due(moment):
                continue
            try:
                value = await task.execute(runner)
                results.append(TaskResult(task, ok=True, value=value))
            except Exception as exc:  # recorded per task — one failure never stops the rest
                results.append(TaskResult(task, ok=False, error=exc))
        return results


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
