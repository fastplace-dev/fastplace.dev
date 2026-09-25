"""Scheduler commands — `schedule:list`/`run`/`work`/`test` (spec #57-#60)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from fastplace.console import console

if TYPE_CHECKING:  # annotations stay lazy; the runtime imports are function-local
    from fastplace.schedule import TaskResult

schedule_app = typer.Typer(help="Scheduled task registry.")


def _project_root() -> Path:
    """The cwd when it is a Fastplace project; a friendly exit otherwise."""
    root = Path.cwd()
    if not (root / "asgi.py").is_file():
        console.print(
            "[red]not inside a Fastplace project[/] — run this from a project root "
            "(the directory containing asgi.py)."
        )
        raise typer.Exit(code=1)
    return root


def _frozen_now(raw: str | None) -> dt.datetime | None:
    """A hidden ``--now`` for deterministic listings/tests; friendly on bad input."""
    if raw is None:
        return None
    try:
        return dt.datetime.fromisoformat(raw)
    except ValueError:
        console.print(
            f"[red]invalid timestamp[/] {raw!r} — expected an ISO form like 2026-01-15T10:10:00"
        )
        raise typer.Exit(code=1) from None


@schedule_app.command("schedule:list")
def schedule_list(
    now: str | None = typer.Option(
        None,
        "--now",
        hidden=True,
        help="Evaluate schedules as of this timestamp instead of the current time.",
    ),
) -> None:
    """List the project's scheduled tasks, their expressions, and next due time."""
    from fastplace.schedule import load_schedule

    schedule = load_schedule(_project_root())
    moment = _frozen_now(now) or dt.datetime.now()
    tasks = schedule.tasks()
    if not tasks:
        console.print("[dim]no scheduled tasks — define some in app/schedule.py[/]")
        return

    from rich.table import Table

    table = Table(title="Scheduled tasks")
    table.add_column("name", style="bold cyan", no_wrap=True)
    table.add_column("expression")
    table.add_column("next due", no_wrap=True)
    for task in tasks:
        table.add_row(task.name, task.expression, task.next_due(moment).strftime("%Y-%m-%d %H:%M"))
    console.print(table)


def _print_results(results: list[TaskResult]) -> bool:
    """One line per result — `ran`/`failed` — and whether anything failed."""
    from rich.markup import escape

    failed = False
    for result in results:
        # Task names and outcomes are data, not markup — render literally.
        if result.ok:
            detail = f" — {result.value}" if result.value is not None else ""
            console.print(f"[green]ran[/] {escape(result.task.name)}{escape(detail)}")
        else:
            failed = True
            console.print(
                f"[red]failed[/] {escape(result.task.name)} — {escape(str(result.error))}"
            )
    return failed


@schedule_app.command("schedule:run")
def schedule_run(
    now: str | None = typer.Option(
        None,
        "--now",
        hidden=True,
        help="Run tasks due at this timestamp instead of the current time.",
    ),
) -> None:
    """Run the tasks that are due now, once, and print each result."""
    import asyncio

    from fastplace.schedule import load_schedule

    schedule = load_schedule(_project_root())
    results = asyncio.run(schedule.run_due(_frozen_now(now)))
    if not results:
        console.print("[dim]no scheduled tasks due[/]")
        return

    if _print_results(results):
        raise typer.Exit(code=1)


@schedule_app.command("schedule:work")
def schedule_work() -> None:
    """Run due tasks every minute in the foreground until Ctrl+C."""
    import asyncio

    from fastplace.schedule import load_schedule, run_worker

    schedule = load_schedule(_project_root())
    tasks = schedule.tasks()
    if not tasks:
        console.print("[dim]no scheduled tasks — define some in app/schedule.py[/]")
        return

    console.print(
        f"schedule worker started — {len(tasks)} task(s); ticking on the minute, Ctrl+C to stop"
    )
    try:
        asyncio.run(run_worker(schedule, on_tick=_print_results))
    except KeyboardInterrupt:
        console.print("\n[dim]worker stopped — goodbye[/]")


@schedule_app.command("schedule:test")
def schedule_test(
    name: str = typer.Argument(..., help="The task name as registered in app/schedule.py."),
) -> None:
    """Run one named task immediately, ignoring its schedule."""
    import asyncio

    from fastplace.schedule import TaskResult, load_schedule

    schedule = load_schedule(_project_root())
    task = schedule.find(name)
    if task is None:
        tasks = schedule.tasks()
        if tasks:
            known = ", ".join(sorted(candidate.name for candidate in tasks))
            console.print(f"[red]unknown task[/] {name!r} — known tasks: {known}")
        else:
            console.print(f"[red]unknown task[/] {name!r} — no tasks defined in app/schedule.py")
        raise typer.Exit(code=1)

    console.print(f"[dim]testing {task.name} ({task.expression}) — due-ness ignored[/]")
    try:
        result = TaskResult(task, ok=True, value=asyncio.run(task.execute()))
    except Exception as exc:
        result = TaskResult(task, ok=False, error=exc)
    _print_results([result])
    if not result.ok:
        raise typer.Exit(code=1)


@schedule_app.command("schedule:preview")
def schedule_preview(
    hours: int = typer.Option(24, "--hours", min=1, help="Window length in hours."),
    now: str | None = typer.Option(
        None,
        "--now",
        hidden=True,
        help="Simulate from this timestamp instead of the current time.",
    ),
    per_task: int = typer.Option(10, "--per-task", min=1, help="Max firings shown per task."),
) -> None:
    """Simulate which tasks fire in the next N hours (nothing runs)."""
    from rich.markup import escape
    from rich.table import Table

    from fastplace.schedule import load_schedule

    moment = _frozen_now(now) or dt.datetime.now()
    tasks = load_schedule(_project_root()).tasks()
    if not tasks:
        console.print("[dim]no scheduled tasks — define some in app/schedule.py[/]")
        return

    horizon = moment + dt.timedelta(hours=hours)
    rows: list[tuple[dt.datetime, str, str]] = []
    capped: list[str] = []
    silent: list[str] = []
    broken: list[str] = []
    for task in tasks:
        fired = 0
        cursor = moment
        try:
            while fired < per_task:
                nxt = task.next_due(cursor)
                if nxt >= horizon:
                    break
                rows.append((nxt, task.name, task.expression))
                fired += 1
                cursor = nxt
        except ValueError:
            # A cron that matches no real date (Feb 30, say) — flag it and
            # keep simulating the tasks behind it.
            broken.append(task.name)
            console.print(
                f"[red]cron '{escape(task.expression)}' never matches a real date[/] "
                f"— skipped '{escape(task.name)}'"
            )
            continue
        if fired == per_task:
            capped.append(task.name)
        elif fired == 0:
            silent.append(task.name)

    for name in silent:
        console.print(f"[dim]{escape(name)}: no firings within the window[/]")

    if not rows:
        console.print("[dim]no firings in the window[/]")
        if broken:
            # Nothing could be computed at all — the schedule is undiagnosable.
            raise typer.Exit(code=1)
        return

    rows.sort(key=lambda row: row[0])
    table = Table(title=f"Firings in the next {hours}h (from {moment:%Y-%m-%d %H:%M})")
    table.add_column("when", style="cyan", no_wrap=True)
    table.add_column("task", style="bold")
    table.add_column("expression", style="dim")
    for when, name, expression in rows:
        table.add_row(when.strftime("%Y-%m-%d %H:%M"), escape(name), escape(expression))
    console.print(table)
    for name in capped:
        console.print(f"[dim]+ more within window for '{escape(name)}'[/]")
