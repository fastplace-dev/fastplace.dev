"""Queue worker commands — `fastplace queue:work`."""

from __future__ import annotations

import asyncio

import typer

from fastplace.config import config, load_env

queue_app = typer.Typer(help="Background queue operations.")


@queue_app.command("queue:work")
def queue_work(
    once: bool = typer.Option(
        False, "--once", help="Process what is queued, then exit (memory driver always does)."
    ),
) -> None:
    """Process queued background jobs from app/jobs/."""
    load_env()
    from fastplace.console import console
    from fastplace.queue import MemoryQueue, import_jobs, queue

    names = import_jobs()
    driver_name = str(config("QUEUE_DRIVER", default="memory"))

    if driver_name == "saq":
        driver = queue()
        worker = driver.build_worker(burst=once)  # type: ignore[attr-defined]
        console.print(
            f"[green]▸[/] saq worker started ({len(names)} job(s) registered) "
            f"{'— burst mode' if once else ''}"
        )
        asyncio.run(worker.start())
        return

    # Memory driver: there is no cross-process broker, so working always
    # means draining what this project queued in-process, then exiting.
    q = queue()
    if not isinstance(q, MemoryQueue):  # pragma: no cover — factory contract
        console.print(f"[red]✗[/] driver '{driver_name}' has no worker loop")
        raise typer.Exit(code=1)

    executed = asyncio.run(q.run_pending())
    if executed:
        console.print(f"[green]✓[/] processed {executed} job(s)")
    else:
        console.print("no pending jobs")
    for failure in q.failures:
        console.print(f"[red]✗[/] {failure.name} failed: {failure.error}")
