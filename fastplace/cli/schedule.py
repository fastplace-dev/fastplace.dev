"""Scheduler commands — `fastplace schedule:list`, `schedule:run` (spec #57, #59)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import typer

from fastplace.console import console

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

    failed = False
    for result in results:
        if result.ok:
            detail = f" — {result.value}" if result.value is not None else ""
            console.print(f"[green]ran[/] {result.task.name}{detail}")
        else:
            failed = True
            console.print(f"[red]failed[/] {result.task.name} — {result.error}")
    if failed:
        raise typer.Exit(code=1)
