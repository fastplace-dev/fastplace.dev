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
    from rich.markup import escape

    from fastplace.console import console
    from fastplace.queue import (
        MemoryQueue,
        SaqQueue,
        clear_restart_sentinel,
        import_jobs,
        queue,
        restart_requested_at,
    )

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
            f"queue '{escape(label)}')" + (" — burst mode" if burst else "")
        )

        async def _run_saq_worker() -> None:
            # A sentinel already pending at start is honored at the first
            # job boundary (the worker's before_process hook consumes it).
            requested = await restart_requested_at() is not None
            await worker.start()
            if requested:
                console.print("[yellow]! restart requested — worker exiting for a replacement")

        asyncio.run(_run_saq_worker())
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

    async def _drain() -> tuple[int, bool]:
        executed = await q.run_pending()
        # The drain stops at a sentinel but never consumes one; this worker
        # is exiting either way, so it consumes it here — otherwise the
        # replacement would immediately exit again.
        requested = await restart_requested_at() is not None
        if requested:
            await clear_restart_sentinel()
        return executed, requested

    executed, restarted = asyncio.run(_drain())
    if restarted:
        console.print("[yellow]! restart requested — worker stopping[/]")
    if executed:
        console.print(f"[green]✓[/] processed {executed} job(s)")
    elif not restarted or not q.pending:
        console.print("no pending jobs")
    if restarted and q.pending:
        # Only restart-related leftovers get a line: an ordinary drain's
        # chained jobs stay silent exactly as they did before restarts.
        console.print(f"[cyan]▸[/] {len(q.pending)} job(s) left for the replacement worker")
    for failure in q.failures:
        # Names and errors are job data, not markup — render them literally.
        console.print(f"[red]✗[/] {escape(failure.name)} failed: {escape(str(failure.error))}")


@queue_app.command("queue:clear")
def queue_clear(
    force: bool = typer.Option(False, "--force", help="Skip the production confirmation prompt."),
) -> None:
    """Delete every pending job without running it."""
    load_env()
    from fastplace.console import console
    from fastplace.queue import queue

    # A destructive command guards unless the environment explicitly says so.
    if str(config("APP_ENV", default="production")).lower() == "production" and not (
        force or typer.confirm("Delete every pending job from the production queue?")
    ):
        console.print("[red]aborted[/] — the pending jobs were left untouched")
        raise typer.Exit(code=1)

    cleared = asyncio.run(queue().clear())
    if cleared:
        console.print(f"[green]cleared[/] {cleared} pending job(s)")
    else:
        console.print("no pending jobs")


# ---------------------------------------------------------------------------
# failed-job inspection — the persisted FailedJobStore (queue_failures.py)
# ---------------------------------------------------------------------------


@queue_app.command("queue:failed")
def queue_failed(
    # min=0 keeps negative values a usage error before any query runs —
    # SQLite reads LIMIT -1 as "no limit", which would dump the whole ledger.
    limit: int = typer.Option(50, "--limit", min=0, help="Rows to show (newest first)."),
    offset: int = typer.Option(0, "--offset", min=0, help="Skip the newest N rows."),
) -> None:
    """List failed jobs recorded by the workers (see also queue:retry)."""
    load_env()
    import json

    from rich.markup import escape
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
        # Job data renders literally — a bracket tag in a name or error must
        # never style the table cell it lands in.
        table.add_row(str(row.id), escape(row.name), escape(args), failed_at, escape(row.error))
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


@queue_app.command("queue:retry")
def queue_retry(
    target: str = typer.Argument("all", help="A failed-job ID to retry, or 'all' (the default)."),
) -> None:
    """Re-dispatch failed job(s) from the persisted failed-job store.

    A successful re-dispatch deletes the record; a dispatch error (broker
    down, handler no longer registered) keeps it for the next attempt and
    exits 1.
    """
    load_env()
    from fastplace.console import console
    from fastplace.queue import import_jobs, queue
    from fastplace.queue_failures import failed_job_store

    import_jobs()  # handlers must be registered before dispatch validates names
    store = failed_job_store()

    async def _retry(row) -> bool:
        from rich.markup import escape

        try:
            await queue().dispatch(row.name, **dict(row.kwargs))
        except Exception as exc:  # noqa: BLE001 — the record stays for the next attempt
            # The name and error are persisted data — render both literally.
            console.print(f"[red]✗[/] failed job {row.id} ({escape(row.name)}): {escape(str(exc))}")
            return False
        await store.delete(row.id)
        console.print(f"[green]✓[/] retried {row.name} (failed job {row.id})")
        return True

    async def _all_rows() -> list:
        # Page the whole ledger BEFORE touching it: deleting while paging
        # would shift later offsets and silently skip records.
        rows: list = []
        offset = 0
        while True:
            page = await store.list(offset=offset, limit=100)
            rows.extend(page)
            if len(page) < 100:
                return rows
            offset += 100

    async def _run(rows: list) -> tuple[int, int]:
        retried = kept = 0
        for row in rows:
            if await _retry(row):
                retried += 1
            else:
                kept += 1
        return retried, kept

    if target != "all":
        try:
            job_id = int(target)
        except ValueError:
            console.print(f"[red]'{target}' is not a failed-job id — pass an ID or 'all'[/]")
            raise typer.Exit(code=1) from None
        row = asyncio.run(store.get(job_id))
        if row is None:
            console.print(f"[red]no failed job with id {job_id}[/]")
            raise typer.Exit(code=1)
        raise typer.Exit(code=0 if asyncio.run(_retry(row)) else 1)

    rows = asyncio.run(_all_rows())
    if not rows:
        console.print("[dim]no failed jobs[/]")
        return
    retried, kept = asyncio.run(_run(rows))
    if kept:
        console.print(f"[red]✗[/] {kept} record(s) kept — dispatch failed")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/] retried {retried} job(s)")


# ---------------------------------------------------------------------------
# worker control — the restart sentinel and depth monitoring
# ---------------------------------------------------------------------------


@queue_app.command("queue:restart")
def queue_restart() -> None:
    """Ask every queue worker to exit at its next job boundary.

    Publishes a timestamped sentinel on the cache store; each worker polls
    it per job, finishes what it can, and exits 0 — a supervisor restarts
    it. Not destructive (no jobs are dropped), so no production guard.
    """
    load_env()
    from fastplace.console import console
    from fastplace.queue import set_restart_sentinel

    asyncio.run(set_restart_sentinel())
    console.print("[green]✓[/] restart requested — workers exit at their next job boundary")


@queue_app.command("queue:monitor")
def queue_monitor(
    queues: list[str] = typer.Argument(..., help="Queue names to check."),
    max_depth: int = typer.Option(
        ..., "--max", min=0, help="Exit 1 when any queue's depth exceeds this."
    ),
) -> None:
    """Report queue depths; exit 1 when any queue exceeds --max.

    Each breach dispatches a ``queue.busy`` domain event (queue, depth,
    max) — a listener or ``@Job`` under that name reacts (alerting,
    scaling); a dispatch that raises only warns, never failing the check.
    On the saq driver each name resolves to its own queue; the memory
    driver has one in-process queue, so every name reports its depth.
    """
    load_env()
    from rich.table import Table

    from fastplace.console import console
    from fastplace.events import DomainEvent, dispatch
    from fastplace.queue import SaqQueue, import_jobs, queue

    import_jobs()  # a "queue.busy" @Job bridges the event to a handler

    driver_name = str(config("QUEUE_DRIVER", default="memory"))

    async def _depth(name: str) -> int:
        if driver_name == "saq":
            return await SaqQueue(name=name).queue_depth()
        return await queue().queue_depth()

    async def _check() -> list[tuple[str, int]]:
        return [(name, await _depth(name)) for name in queues]

    depths = asyncio.run(_check())

    table = Table(box=None, header_style="bold")
    table.add_column("queue", style="bold")
    table.add_column("depth", justify="right")
    table.add_column("max", justify="right")
    for name, depth in depths:
        table.add_row(name, str(depth), str(max_depth))
    console.print(table)

    breaches = [(name, depth) for name, depth in depths if depth > max_depth]
    for name, depth in breaches:
        console.print(f"[red]✗[/] {name} exceeds --max ({depth} > {max_depth})")
        try:
            asyncio.run(
                dispatch(
                    DomainEvent("queue.busy", {"queue": name, "depth": depth, "max": max_depth})
                )
            )
        except Exception as exc:  # noqa: BLE001 — a broken listener never masks the verdict
            console.print(f"[yellow]! queue.busy dispatch failed for '{name}': {exc}")
    if breaches:
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/] all queue(s) within --max {max_depth}")
