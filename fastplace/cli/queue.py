"""Queue worker commands — `fastplace queue:work`."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from fastplace.config import config, load_env

if TYPE_CHECKING:  # annotations stay lazy; runtime imports remain function-local
    from fastplace.cli._doctor import Check


def _project_root() -> Path:
    """Nearest ancestor containing asgi.py, or a red exit (schedule.py guard)."""
    from fastplace.console import console

    candidate = Path.cwd()
    for directory in (candidate, *candidate.parents):
        if (directory / "asgi.py").is_file():
            return directory
    console.print(
        "[red]not inside a Fastplace project[/] — run this from a project root "
        "(the directory containing asgi.py)."
    )
    raise typer.Exit(code=1)


queue_app = typer.Typer(help="Background queue operations.")

#: saq burst mode refuses to construct without a positive dequeue timeout
#: (``Worker.__init__`` raises), so every burst worker gets this default.
_BURST_DEQUEUE_TIMEOUT = 1.0

#: The one saq 0.26 shutdown line that is noise by construction (q2-G8).
#: ``Worker.stop()`` gathers the upkeep tasks with
#: ``timeout=shutdown_grace_period_s or 0`` — default 0 — and the sweep poll
#: sleeps on its full 60s interval regardless of the stop event, so this
#: warning fires on every exit, cleanly drained bursts included. Raising the
#: grace cannot fix it (any sane grace still sits under that 60s sleep, so
#: the warning fires anyway after hanging the exit for the full grace);
#: cancellation itself completes in microseconds, and a genuinely stuck task
#: still surfaces through the distinct follow-up line ("did not finish
#: cancellation in time"), which this filter lets through. Scoped to the
#: worker's lifetime only — the process's other saq logging is untouched.
_SAQ_GRACE_NOISE = (
    "Some tasks did not finish within the shutdown grace period, requesting cancellation"
)

#: Installed saq logs its worker noise on ``logging.getLogger("saq")`` — not
#: a "saq.worker" child (verified against the installed 0.26.4 source). The
#: filter must sit on that exact logger: logger-level filters apply only to
#: records logged through THAT logger, never to children or parents.
_SAQ_LOGGER_NAME = "saq"


class _SuppressSaqGraceNoise(logging.Filter):
    """Drop saq's structural shutdown false-positive for one worker run."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage() != _SAQ_GRACE_NOISE


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

        async def _scan_aborted() -> None:
            """Companion scan — crash-loss visibility (q2-G2).

            One pass at startup surfaces what the previous worker's death
            cost immediately; a background task repeats it every 60s (the
            sweeper aborts a dead worker's jobs ~90s after the crash, so a
            single startup pass would miss mid-run deaths). Best-effort: a
            failing scan is a warning line, never a reason to stop working.
            """
            try:
                lost = await driver.record_aborted_jobs()  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 — the worker outranks the scan
                console.print(f"[yellow]! aborted-job scan failed: {escape(str(exc))}")
                return
            if lost:
                console.print(
                    f"[yellow]! recorded {len(lost)} aborted job(s) — "
                    "a worker died mid-job (see fastplace queue:failed):"
                )
                for lost_name in lost:
                    console.print(f"[yellow]  · {escape(lost_name)}")

        async def _companion_scan() -> None:
            while True:
                await asyncio.sleep(60)
                await _scan_aborted()

        async def _run_saq_worker() -> None:
            # A sentinel already pending at start is honored at the first
            # job boundary (the worker's after_process hook consumes it).
            await _scan_aborted()  # losses from the previous worker, now
            companion = asyncio.create_task(_companion_scan())
            requested = await restart_requested_at() is not None
            saq_logger = logging.getLogger(_SAQ_LOGGER_NAME)
            grace_filter = _SuppressSaqGraceNoise()
            saq_logger.addFilter(grace_filter)
            try:
                await worker.start()
            finally:
                saq_logger.removeFilter(grace_filter)
                companion.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await companion
            # The restart contract's exit path, checked unconditionally — a
            # sentinel published mid-run was invisible to the start sample,
            # and the check must not be. The hook normally consumes it at
            # the job boundary; a sentinel surviving a CLEAN exit means the
            # worker never reached one (idle, or the hook missed) — this
            # process exiting is itself the restart completing for it, so
            # consume here, mirroring the memory driver's drain. Only a
            # clear that fails turns the exit non-zero.
            if await restart_requested_at() is not None:
                requested = True
                try:
                    await clear_restart_sentinel()
                except Exception as exc:  # noqa: BLE001 — reported, then non-zero exit
                    console.print(f"[red]✗[/] restart sentinel clear failed: {escape(str(exc))}")
                    raise typer.Exit(code=1) from exc
            if requested:
                console.print(
                    "[yellow]! restart requested — worker exited cleanly for its replacement"
                )

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
        # replacement would immediately exit again. A failed clear is fatal
        # to the command, not swallowed: the restart contract depends on it.
        requested = await restart_requested_at() is not None
        if requested:
            try:
                await clear_restart_sentinel()
            except Exception as exc:  # noqa: BLE001 — reported, then non-zero exit
                console.print(f"[red]✗[/] restart sentinel clear failed: {escape(str(exc))}")
                raise typer.Exit(code=1) from exc
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

    async def _probe(name: str) -> tuple[int, int]:
        """(depth, active) for one queue — saq reports both; memory has no
        cross-process concept of "running now", so active is honestly 0."""
        if driver_name == "saq":
            driver = SaqQueue(name=name)
            return await driver.queue_depth(), await driver.active_depth()
        mem = queue()
        return await mem.queue_depth(), 0

    async def _check() -> list[tuple[str, int, int]]:
        return [(name, *await _probe(name)) for name in queues]

    depths = asyncio.run(_check())

    table = Table(box=None, header_style="bold")
    table.add_column("queue", style="bold")
    table.add_column("depth", justify="right")
    table.add_column("active", justify="right")
    table.add_column("max", justify="right")
    for name, depth, active in depths:
        table.add_row(name, str(depth), str(active), str(max_depth))
    console.print(table)

    breaches = [(name, depth) for name, depth, _active in depths if depth > max_depth]
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


@queue_app.command("queue:list")
def queue_list() -> None:
    """List every registered job handler discovered in app/jobs/."""
    from rich.markup import escape
    from rich.table import Table

    from fastplace.console import console
    from fastplace.queue import effective_job_options, import_jobs, jobs

    load_env()
    root = _project_root()
    import_jobs(root)
    registry = jobs()

    console.print(
        f"[dim]queue driver:[/] [bold]{escape(str(config('QUEUE_DRIVER', default='memory')))}[/]"
    )
    if not registry:
        console.print("[dim]no registered jobs — define handlers with @Job in app/jobs/[/]")
        return

    table = Table(title="Registered jobs")
    table.add_column("name", style="bold cyan", no_wrap=True)
    table.add_column("module")
    table.add_column("signature")
    # no_wrap keeps a one-line docstring on one line — a folded description
    # would split the sentence across cells (the signature may fold instead).
    table.add_column("description", style="dim", no_wrap=True)
    # The effective envelope (retries×timeout a dispatch would carry) —
    # surface the resolved numbers so nobody decodes @Job/env/defaults by
    # hand to learn what a job's retry budget actually is.
    table.add_column("envelope", justify="right", no_wrap=True)
    from fastplace.errors import ConfigurationError

    for name in sorted(registry):
        entry = registry[name]
        doc = (inspect.getdoc(entry.fn) or "").strip().splitlines()
        try:
            opts = effective_job_options(entry)
        except ConfigurationError as exc:
            # A misconfigured QUEUE_* env fails dispatch loudly; listing must
            # surface the same finding as a red line, not a traceback.
            console.print(f"[red]✗[/] {escape(str(exc))}")
            raise typer.Exit(code=1) from exc
        envelope = f"{opts['retries']}×{opts['timeout']:g}s"
        if opts["backoff"]:
            envelope += f" +{opts['backoff']:g}s"
        table.add_row(
            escape(name),
            escape(entry.fn.__module__),
            escape(str(inspect.signature(entry.fn))),
            escape(doc[0]) if doc else "[dim]—[/]",
            escape(envelope),
        )
    console.print(table)


@queue_app.command("queue:jobs")
def queue_jobs(
    status: str = typer.Option(
        "all", "--status", help="queued | active | scheduled | incomplete | all"
    ),
) -> None:
    """List jobs currently on the active queue driver."""
    import asyncio
    import datetime as dt
    import json
    import time

    from rich.markup import escape
    from rich.table import Table

    from fastplace.config import load_env
    from fastplace.console import console

    # The factory import is the test seam: patching fastplace.queue.queue
    # swaps the store this command reads, whatever the env says.
    from fastplace.queue import import_jobs
    from fastplace.queue import queue as queue_factory

    load_env()
    root = _project_root()
    import_jobs(root)

    if status not in ("queued", "active", "scheduled", "incomplete", "all"):
        console.print(
            f"[red]unknown status '{escape(status)}'[/] — "
            "queued | active | scheduled | incomplete | all"
        )
        raise typer.Exit(code=1)

    async def _rows() -> list[tuple[str, str, str, str, str]]:
        store = queue_factory()
        rows: list[tuple[str, str, str, str, str]] = []
        # Driver detection inspects the store, not the env: the memory
        # driver carries its in-process pending deque; a saq store pages
        # the broker instead.
        pending = getattr(store, "pending", None)
        if pending is not None:  # memory driver — every row is 'queued'
            for entry in list(pending):
                if status in ("all", "queued", "incomplete"):
                    rows.append(("queued", entry.name, "-", "-", json.dumps(entry.kwargs)))
        else:  # saq driver
            from saq.job import Status

            # saq knows new/queued/active/aborting/aborted/failed/complete.
            # "scheduled" and "incomplete" are CLI concepts translated here:
            # passing them through raw makes the broker filter match nothing.
            statuses: Any
            if status == "all":
                # iter_jobs does set(statuses) — None would crash it there,
                # so "all" passes the complete saq status list instead.
                statuses = list(Status)
            elif status == "incomplete":
                statuses = [Status.NEW, Status.QUEUED, Status.ACTIVE, Status.ABORTING]
            elif status == "scheduled":
                # A delayed job sits in NEW/QUEUED with a future epoch; an
                # ACTIVE job is already running, so it is never scheduled.
                # Fetch those statuses and post-filter on the fire time.
                statuses = [Status.NEW, Status.QUEUED]
            else:
                statuses = [Status(status)]
            broker: Any = getattr(store, "queue", None)
            now = time.time()
            async for job in broker.iter_jobs(statuses=statuses, batch_size=500):
                if status == "scheduled":
                    # Due-now (or already running) jobs are not scheduled.
                    if (getattr(job, "scheduled", 0) or 0) <= now:
                        continue
                # saq's Job.scheduled is absolute epoch SECONDS (its own
                # docstring) — the same fact dispatch_delayed already encodes.
                scheduled = getattr(job, "scheduled", 0)
                when = (
                    dt.datetime.fromtimestamp(scheduled).strftime("%Y-%m-%d %H:%M:%S")
                    if scheduled
                    else "-"
                )
                rows.append(
                    (
                        str(getattr(job, "status", "-")),
                        str(job.function),
                        str(getattr(job, "attempts", "-")),
                        when,
                        json.dumps(job.kwargs)[:120],
                    )
                )
        return rows

    rows = asyncio.run(_rows())
    if not rows:
        console.print("[dim]no jobs[/]")
        return
    table = Table(title="Queue jobs")
    table.add_column("status")
    table.add_column("function", style="cyan", no_wrap=True)
    table.add_column("attempts", justify="right")
    table.add_column("scheduled for")
    table.add_column("kwargs", style="dim")
    # Job payloads are data, not markup — a bracket tag in a name or kwargs
    # must render literally, never style the cell it lands in.
    for row in rows:
        table.add_row(*(escape(cell) for cell in row))
    console.print(table)


@queue_app.command("queue:show")
def queue_show(job_id: str = typer.Argument(..., help="Failed-job id.")) -> None:
    """Show one failed job at full fidelity (full error, registration state)."""
    import asyncio
    import json

    from rich.markup import escape
    from rich.panel import Panel
    from rich.table import Table

    from fastplace.console import console

    load_env()
    root = _project_root()

    # The id arrives as a raw string — convert manually so 'abc' is a red
    # message, never a typer usage error (mirrors queue:forget).
    try:
        numeric_id = int(job_id)
    except ValueError:
        console.print(
            f"[red]'{escape(job_id)}' is not a job id[/] — "
            "expected a number, e.g. fastplace queue:show 3"
        )
        raise typer.Exit(code=1) from None

    from fastplace.queue_failures import failed_job_store, reset_failed_job_store

    reset_failed_job_store()

    async def _fetch():
        return await failed_job_store().get(numeric_id)

    record = asyncio.run(_fetch())
    if record is None:
        console.print(f"[red]no failed job #{numeric_id}[/]")
        raise typer.Exit(code=1)

    # Registration is the repair decision: a handler the code no longer
    # defines is retried into the same failure, so say so in yellow.
    from fastplace.queue import import_jobs, jobs

    import_jobs(root)
    registered = record.name in jobs()

    body = Table(show_header=False, box=None)
    body.add_column(style="dim")
    body.add_column()
    # Every persisted value is data, not markup — escape it all literally.
    body.add_row("job", escape(str(record.name)))
    body.add_row("failed at", record.failed_at.isoformat(sep=" ", timespec="seconds"))
    body.add_row("kwargs", escape(json.dumps(record.kwargs, indent=2)))
    body.add_row(
        "handler",
        "[green]registered[/]" if registered else "[yellow]no longer registered[/]",
    )
    console.print(Panel(body, title=f"[red]failed job #{numeric_id}[/]"))
    console.print(escape(str(record.error)))
    console.print(f"[cyan]fastplace queue:retry {numeric_id}[/]")


@queue_app.command("queue:dispatch")
def queue_dispatch(
    name: str = typer.Argument(..., help="Registered job name."),
    kwargs_json: str = typer.Option("{}", "--kwargs", help="JSON object of handler kwargs."),
    delay: float = typer.Option(0.0, "--delay", help="Seconds to wait before the job runs."),
    retries: int = typer.Option(None, "--retries", min=1, help="Total attempts for this dispatch."),
    timeout: float = typer.Option(
        None, "--timeout", help="Per-attempt timeout seconds for this dispatch."
    ),
    queue_name: str = typer.Option(None, "--queue", help="Named saq queue to enqueue on."),
) -> None:
    """Enqueue one registered job without running it now."""
    import asyncio
    import json

    from rich.markup import escape

    from fastplace.console import console
    from fastplace.queue import import_jobs, jobs
    from fastplace.queue import queue as queue_factory

    load_env()
    root = _project_root()
    import_jobs(root)
    if name not in jobs():
        console.print(f"[red]unknown job '{escape(name)}'[/] — see [cyan]fastplace queue:list[/]")
        raise typer.Exit(code=1)

    try:
        parsed = json.loads(kwargs_json)
    except ValueError as exc:
        console.print(f"[red]invalid --kwargs JSON[/] — {escape(str(exc))}")
        raise typer.Exit(code=1) from None
    if not isinstance(parsed, dict):
        console.print("[red]--kwargs must be a JSON object[/] of handler kwargs")
        raise typer.Exit(code=1)

    # The factory import is the test seam: patching fastplace.queue.queue
    # swaps the store this command dispatches to, whatever the env says.
    store: Any = queue_factory()
    # Driver detection inspects the store, not the env (mirrors
    # queue:jobs): the memory driver carries its in-process pending
    # deque, a saq store pages the broker instead.
    if hasattr(store, "pending"):
        # q1-G5 honesty: nothing ever comes back to run a job this command
        # queues on the memory driver — the pending deque dies with this
        # process the moment the command exits. Refuse before anything
        # lands on the queue instead of printing "Dispatched" over a job
        # that silently never runs.
        console.print(
            "[red]the memory driver cannot accept CLI dispatches[/] — a job queued in this "
            "process dies with it; set QUEUE_DRIVER=saq (redis) so a queue:work process runs it"
        )
        raise typer.Exit(code=1)

    async def _run() -> Any:
        # The builder carries the envelope options (queue().job(name, ...)):
        # handler kwargs ride dispatch() and must never collide with option
        # names, so the two namespaces stay separate end to end.
        return await store.job(
            name,
            retries=retries,
            timeout=timeout,
            delay=delay if delay > 0 else None,
            queue=queue_name,
        ).dispatch(**parsed)

    handle = asyncio.run(_run())
    suffix = "" if delay <= 0 else f" (in {delay:g}s)"
    if handle is None:
        # Only a unique= dispatch can land here — the driver suppressed an
        # identical unresolved job. The CLI never sets unique=, so this is
        # future-proofing the honest output if it ever does.
        console.print(
            f"[yellow]suppressed '{escape(name)}'[/] — an identical job is already queued"
        )
        return
    console.print(
        f"[green]Dispatched '{escape(name)}'[/]{suffix} "
        f"(job {escape(str(getattr(handle, 'key', '?')))})"
    )


# ---------------------------------------------------------------------------
# queue:health — roadmap data plane doctor
# ---------------------------------------------------------------------------


def _check_queue_driver() -> Check:
    """QUEUE_DRIVER resolves to a known driver (memory or saq)."""
    from fastplace.cli._doctor import Check
    from fastplace.errors import ConfigurationError
    from fastplace.queue import queue

    try:
        driver = queue()
    except ConfigurationError as exc:
        wanted = str(config("QUEUE_DRIVER", default=""))
        return Check(
            "driver",
            "fail",
            f"unknown QUEUE_DRIVER '{wanted}' — expected memory or saq ({exc})",
            "set QUEUE_DRIVER=memory or QUEUE_DRIVER=saq in .env",
        )
    return Check("driver", "pass", type(driver).__name__.lower().removesuffix("queue") or "memory")


def _check_redis_reachable() -> Check:
    """Driver=saq needs a reachable redis; memory needs nothing."""
    import asyncio

    from fastplace.cli._doctor import Check
    from fastplace.config import config as cfg

    driver = str(cfg("QUEUE_DRIVER", default="memory")).lower()
    if driver != "saq":
        return Check("redis", "pass", "not used (driver is memory)")
    try:
        import redis.asyncio as aioredis
    except ImportError:
        return Check(
            "redis",
            "fail",
            "redis library not installed",
            'pip install "fastplace[queue]"',
        )
    url = str(cfg("QUEUE_REDIS_URL", default="redis://localhost:6379/0"))

    async def _ping() -> None:
        client = aioredis.from_url(url)
        try:
            await client.ping()
        finally:
            await client.aclose()

    try:
        asyncio.run(_ping())
    except Exception as exc:  # noqa: BLE001 — unreachability is itself the finding
        return Check(
            "redis",
            "fail",
            f"{url} unreachable ({type(exc).__name__})",
            "check QUEUE_REDIS_URL and that redis is running",
        )
    return Check("redis", "pass", f"{url} reachable")


def _check_depth() -> Check:
    """Current queue depth via the configured driver."""
    import asyncio

    from fastplace.cli._doctor import Check
    from fastplace.queue import queue

    async def _probe() -> int:
        return int(await queue().queue_depth())

    try:
        depth = asyncio.run(_probe())
    except Exception as exc:  # noqa: BLE001 — unreachable queue is the finding
        return Check(
            "depth",
            "fail",
            f"cannot read queue depth: {type(exc).__name__}",
            "check QUEUE_REDIS_URL and that redis is running",
        )
    detail = f"{depth} pending job(s)"
    if depth > 1000:
        return Check(
            "depth", "warn", detail, "fastplace queue:clear or queue:work to drain the backlog"
        )
    return Check("depth", "pass", detail)


def _check_restart_sentinel() -> Check:
    """A published restart sentinel means workers exit at the next boundary."""
    import asyncio

    from fastplace.cli._doctor import Check
    from fastplace.queue import restart_requested_at

    requested = asyncio.run(restart_requested_at())
    if requested is None:
        return Check("restart", "pass", "no restart pending")
    return Check(
        "restart",
        "warn",
        f"restart requested at {requested.isoformat(sep=' ', timespec='seconds')}",
        "workers exit at the next job boundary — queue:restart already issued",
    )


def _check_failed_jobs() -> Check:
    """Failed-job ledger count; any row is a WARN with the retry fix."""
    import asyncio

    from fastplace.cli._doctor import Check
    from fastplace.queue_failures import failed_job_store, reset_failed_job_store

    reset_failed_job_store()

    async def _count() -> int:
        return len(await failed_job_store().list(limit=10_000))

    try:
        count = asyncio.run(_count())
    except Exception as exc:  # noqa: BLE001 — unreachable ledger is the finding
        return Check(
            "failed_jobs",
            "fail",
            f"cannot read the failed-job ledger: {type(exc).__name__}",
            "check DATABASE_URL and that the database is running",
        )
    if count:
        return Check(
            "failed_jobs",
            "warn",
            f"{count} failed job(s)",
            "fastplace queue:retry <id> (inspect with queue:failed)",
        )
    return Check("failed_jobs", "pass", "0 failed jobs")


def _check_schedule(root: Path) -> Check:
    """app/schedule.py loads; absent is a fresh project's empty schedule."""
    from fastplace.cli._doctor import Check
    from fastplace.schedule import load_schedule

    if not (root / "app" / "schedule.py").is_file():
        return Check("schedule", "pass", "no app/schedule.py — no scheduled tasks")
    try:
        tasks = load_schedule(root).tasks()
    except Exception as exc:  # noqa: BLE001 — broken project code is the finding
        return Check(
            "schedule",
            "fail",
            f"{type(exc).__name__}: {exc}",
            "fix app/schedule.py — it must define def schedule(s: Schedule) -> None",
        )
    plural = "task" if len(tasks) == 1 else "tasks"
    return Check("schedule", "pass", f"{len(tasks)} {plural}")


def _check_defaults() -> Check:
    """The envelope every undecorated dispatch would carry (informational).

    Surfacing the resolved QUEUE_TRIES/QUEUE_TIMEOUT/QUEUE_BACKOFF/QUEUE_TTL
    floor here — right next to the driver/depth checks — means a team reads
    its effective retry budget without decoding config layers by hand. A
    misconfigured value surfaces as its own FAIL through dispatch-time
    validation; this row only reports.
    """
    from fastplace.cli._doctor import Check
    from fastplace.queue import effective_job_options

    opts = effective_job_options()
    detail = (
        f"retries={opts['retries']} timeout={opts['timeout']:g}s "
        f"backoff={opts['backoff']:g}s ttl={opts['ttl']}s"
    )
    return Check(
        "defaults", "pass", detail, "QUEUE_TRIES / QUEUE_TIMEOUT / QUEUE_BACKOFF / QUEUE_TTL"
    )


def _check_dashboard() -> Check:
    """QUEUE_DASHBOARD_ENABLED vs driver — the mount needs the saq queue.

    Flag on + memory driver means the dashboard silently never mounts; the
    row warns so the operator sees the combo instead of hunting a 404.
    """
    from fastplace.cli._doctor import Check

    enabled = str(config("QUEUE_DASHBOARD_ENABLED", default=False)).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    driver = str(config("QUEUE_DRIVER", default="memory"))
    if not enabled:
        return Check("dashboard", "pass", "off")
    if driver != "saq":
        return Check(
            "dashboard",
            "warn",
            f"QUEUE_DASHBOARD_ENABLED=true but QUEUE_DRIVER={driver} — not mounted",
            "set QUEUE_DRIVER=saq or turn the dashboard flag off",
        )
    path = str(config("QUEUE_DASHBOARD_PATH", default="/queue-dashboard"))
    return Check("dashboard", "pass", f"mounted at {path}")


def _check_aborted_jobs() -> Check:
    """ABORTED jobs sitting in redis — crash-loss made visible (q2-G5).

    Read-only on purpose: this row counts what the sweeper aborted, it does
    not ledger anything (``queue:work``'s companion scan owns recording).
    Memory driver has nothing to scan — the row states that honestly.
    """
    import asyncio

    from fastplace.cli._doctor import Check
    from fastplace.queue import queue

    try:
        driver = queue()
    except Exception:  # noqa: BLE001 — the driver row already reports this
        return Check("aborted", "pass", "not checked (driver unresolved)")
    if not hasattr(driver, "aborted_jobs"):
        return Check("aborted", "pass", "not used (driver is memory)")
    try:
        aborted = asyncio.run(driver.aborted_jobs())  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — unreachable redis is the finding
        return Check(
            "aborted",
            "warn",
            f"cannot scan aborted jobs: {type(exc).__name__}",
            "check QUEUE_REDIS_URL and that redis is running",
        )
    if aborted:
        return Check(
            "aborted",
            "warn",
            f"{len(aborted)} aborted job(s) in redis — worker crash-loss",
            "fastplace queue:failed (queue:work's scan records them)",
        )
    return Check("aborted", "pass", "0 aborted jobs")


@queue_app.command("queue:health")
def queue_health() -> None:
    """Queue stack diagnosis: driver, redis, depth, restart, failures, schedule."""
    from fastplace.cli._doctor import run_checks
    from fastplace.config import reset_config

    root = _project_root()
    load_env(root / ".env")
    # Bind the config registry to the invoked project (doctor umbrella pattern).
    reset_config(root)

    # Named closures (not lambdas) so a raising check degrades to a readable
    # row name — run_checks derives it from __name__.
    def _schedule_check() -> Check:
        return _check_schedule(root)

    code = run_checks(
        "Queue doctor",
        [
            _check_queue_driver,
            _check_redis_reachable,
            _check_depth,
            _check_restart_sentinel,
            _check_failed_jobs,
            _check_aborted_jobs,
            _check_dashboard,
            _check_defaults,
            _schedule_check,
        ],
    )
    raise typer.Exit(code=code)
