"""Queue worker commands — `fastplace queue:work`."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import typer

from fastplace.config import config, load_env

queue_app = typer.Typer(help="Background queue operations.")

#: saq burst mode refuses to construct without a positive dequeue timeout
#: (``Worker.__init__`` raises), so every burst worker gets this default.
_BURST_DEQUEUE_TIMEOUT = 1.0

#: CLI option → the saq ``Worker`` parameter it would map to. Whether it
#: actually maps is decided against the INSTALLED saq's ``Worker.__init__``
#: signature — an option absent there is reported as a no-op instead of
#: crashing the worker build or silently pretending to work.
_OPTION_TO_SAQ_KWARG = {
    "tries": "tries",
    "timeout": "timeout",
    "sleep": "sleep",
    "max_jobs": "max_burst_jobs",
}


def _installed_saq_worker_params() -> set[str]:
    """Keyword parameter names of the installed saq ``Worker.__init__``."""
    from saq import Worker

    return set(inspect.signature(Worker.__init__).parameters)


def _translate_worker_options(
    options: dict[str, Any], params: set[str]
) -> tuple[dict[str, Any], list[str]]:
    """Split runtime options into (worker kwargs, no-op warning lines).

    An option becomes a worker kwarg only when the installed saq Worker
    accepts it; otherwise it comes back as a warning naming the installed
    version, so one invocation behaves predictably across saq upgrades.
    """
    import saq

    version = getattr(saq, "__version__", "unknown")
    kwargs: dict[str, Any] = {}
    warnings: list[str] = []
    for option, kwarg in _OPTION_TO_SAQ_KWARG.items():
        value = options.get(option)
        if value is None:
            continue
        if kwarg in params:
            kwargs[kwarg] = value
        else:
            flag = option.replace("_", "-")
            warnings.append(
                f"[yellow]!--{flag}={value}:[/] no-op — saq {version} "
                f"Worker has no '{kwarg}' parameter"
            )
    return kwargs, warnings


@queue_app.command("queue:work")
def queue_work(
    once: bool = typer.Option(
        False, "--once", help="Process what is queued, then exit (memory driver always does)."
    ),
    tries: int | None = typer.Option(
        None,
        "--tries",
        help="Retry attempts per job — no-op: retries are set per job at dispatch.",
    ),
    timeout: float | None = typer.Option(
        None,
        "--timeout",
        help="Per-job timeout in seconds — no-op: timeout is set per job at dispatch.",
    ),
    sleep: float | None = typer.Option(
        None,
        "--sleep",
        help="Seconds to idle between empty polls — no-op: installed saq has no worker sleep.",
    ),
    max_jobs: int | None = typer.Option(
        None,
        "--max-jobs",
        help="Stop after this many jobs (saq burst mode: caps max_burst_jobs).",
    ),
    queue_name: str | None = typer.Option(
        None,
        "--queue",
        help="Work this queue name instead of QUEUE_NAME (saq driver).",
    ),
    stop_when_empty: bool = typer.Option(
        False,
        "--stop-when-empty",
        help="Exit once the queue drains (saq burst mode; the memory driver always does).",
    ),
) -> None:
    """Process queued background jobs from app/jobs/."""
    load_env()
    from fastplace.console import console
    from fastplace.queue import MemoryQueue, SaqQueue, import_jobs, queue

    names = import_jobs()
    driver_name = str(config("QUEUE_DRIVER", default="memory"))

    if driver_name == "saq":
        params = _installed_saq_worker_params()
        kwargs, warnings = _translate_worker_options(
            {"tries": tries, "timeout": timeout, "sleep": sleep, "max_jobs": max_jobs},
            params,
        )
        for warning in warnings:
            console.print(warning)
        burst = once or stop_when_empty
        if burst:
            kwargs["burst"] = True
            if "dequeue_timeout" in params and "dequeue_timeout" not in kwargs:
                # Burst mode is the one place saq demands a positive
                # dequeue timeout, or Worker.__init__ refuses to build.
                kwargs["dequeue_timeout"] = _BURST_DEQUEUE_TIMEOUT
        driver = SaqQueue(name=queue_name) if queue_name else queue()
        worker = driver.build_worker(**kwargs)  # type: ignore[attr-defined]
        label = queue_name or config("QUEUE_NAME", default="fastplace")
        console.print(
            f"[green]▸[/] saq worker started ({len(names)} job(s) registered, "
            f"queue '{label}')" + (" — burst mode" if burst else "")
        )
        asyncio.run(worker.start())
        return

    # Memory driver: there is no cross-process broker, so working always
    # means draining what this project queued in-process, then exiting.
    q = queue()
    if not isinstance(q, MemoryQueue):  # pragma: no cover — factory contract
        console.print(f"[red]✗[/] driver '{driver_name}' has no worker loop")
        raise typer.Exit(code=1)

    # Worker-loop tuning cannot apply to a drain-once driver; say so per
    # option instead of accepting the flags silently.
    for option, value in (
        ("tries", tries),
        ("timeout", timeout),
        ("sleep", sleep),
        ("max-jobs", max_jobs),
        ("queue", queue_name),
    ):
        if value is not None:
            console.print(
                f"[yellow]!--{option}={value}:[/] no-op on the memory driver — "
                "it drains what was dispatched and exits"
            )

    executed = asyncio.run(q.run_pending())
    if executed:
        console.print(f"[green]✓[/] processed {executed} job(s)")
    else:
        console.print("no pending jobs")
    for failure in q.failures:
        console.print(f"[red]✗[/] {failure.name} failed: {failure.error}")


# ---------------------------------------------------------------------------
# failed-job inspection — the persisted FailedJobStore (queue_failures.py)
# ---------------------------------------------------------------------------


@queue_app.command("queue:failed")
def queue_failed(
    limit: int = typer.Option(50, "--limit", help="Rows to show (newest first)."),
    offset: int = typer.Option(0, "--offset", help="Skip the newest N rows."),
) -> None:
    """List failed jobs recorded by the workers (see also queue:retry)."""
    load_env()
    import json

    from rich.table import Table

    from fastplace.console import console
    from fastplace.queue_failures import failed_job_store

    rows = asyncio.run(failed_job_store().list(offset=offset, limit=limit))
    if not rows:
        console.print("[dim]no failed jobs[/]")
        return
    table = Table(box=None, header_style="bold")
    table.add_column("id", justify="right", style="cyan", no_wrap=True)
    table.add_column("job", style="bold", no_wrap=True)  # identifiers never wrap
    table.add_column("args", style="dim")
    table.add_column("failed at (UTC)", no_wrap=True)
    table.add_column("error")
    for row in rows:
        args = json.dumps(row.kwargs, sort_keys=True, default=str)
        failed_at = row.failed_at.isoformat(sep=" ", timespec="seconds")
        table.add_row(str(row.id), row.name, args, failed_at, row.error)
    console.print(table)


@queue_app.command("queue:forget")
def queue_forget(
    job_id: str = typer.Argument(..., help="ID of the failed-job record to delete."),
) -> None:
    """Delete one failed-job record by ID."""
    load_env()
    from fastplace.console import console
    from fastplace.queue_failures import failed_job_store

    try:
        parsed = int(job_id)
    except ValueError:
        console.print(f"[red]'{job_id}' is not a failed-job id (a number)[/]")
        raise typer.Exit(code=1) from None
    deleted = asyncio.run(failed_job_store().delete(parsed))
    if not deleted:
        console.print(f"[red]no failed job with id {parsed}[/]")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/] forgot failed job {parsed}")


@queue_app.command("queue:flush")
def queue_flush(
    force: bool = typer.Option(False, "--force", help="Skip the production confirmation prompt."),
) -> None:
    """Delete every failed-job record."""
    load_env()
    from fastplace.console import console
    from fastplace.queue_failures import failed_job_store

    # A destructive command guards unless the environment explicitly says so.
    if str(config("APP_ENV", default="production")).lower() == "production" and not (
        force or typer.confirm("Delete every failed-job record from production?")
    ):
        console.print("[red]aborted[/] — the failed-job records were left untouched")
        raise typer.Exit(code=1)

    removed = asyncio.run(failed_job_store().flush())
    console.print(f"[green]flushed[/] {removed} failed-job record(s)")


@queue_app.command("queue:prune-failed")
def queue_prune_failed(
    hours: int = typer.Option(
        24, "--hours", min=0, help="Delete records older than this many hours."
    ),
) -> None:
    """Delete failed-job records older than --hours (default: 24)."""
    load_env()
    from datetime import timedelta

    from fastplace.console import console
    from fastplace.queue_failures import failed_job_store, utcnow

    before = utcnow() - timedelta(hours=hours)
    removed = asyncio.run(failed_job_store().prune(before))
    console.print(f"[green]pruned[/] {removed} failed-job record(s) older than {hours}h")
