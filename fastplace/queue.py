"""Queue — background job dispatch with memory and SAQ drivers.

Handlers are declared with ``@Job`` under ``app/jobs/``::

    # app/jobs/email_job.py
    from fastplace.queue import Job

    @Job()
    async def welcome_email(user_id: int):
        ...

    # anywhere
    await queue().dispatch("welcome_email", user_id=7)

``queue()`` picks the driver from ``QUEUE_DRIVER``: ``memory`` (default —
dispatch queues in-process; ``fastplace queue:work --once`` drains it, the
whole story for non-redis dev) or ``saq`` (production, backed by redis). The
SAQ queue and its redis connections are built lazily — importing or
constructing the driver never touches the network.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import pkgutil
import time
from collections import deque
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from fastplace.config import config
from fastplace.errors import ConfigurationError
from fastplace.queue_failures import format_error, record_failure, utcnow

logger = logging.getLogger("fastplace.queue")

#: A job handler — always async; kwargs arrive from dispatch.
JobFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class _JobEntry:
    """One registered handler under its dispatch name."""

    name: str
    fn: JobFn


#: name → handler registry, populated by @Job at import time.
registry: dict[str, _JobEntry] = {}


def registered_jobs() -> list[str]:
    """Sorted names of every registered handler."""
    return sorted(registry)


def jobs() -> dict[str, _JobEntry]:
    """The live registry (tests and the worker builder read it)."""
    return registry


def reset_registry() -> None:
    """Clear the registry — test isolation."""
    registry.clear()


def import_jobs(project_root: str | Path | None = None) -> list[str]:
    """Import every module under ``app/jobs/`` so their @Job decorators run."""
    import sys

    root = Path(project_root) if project_root else Path.cwd()
    jobs_dir = root / "app" / "jobs"
    if not jobs_dir.is_dir():
        return []
    root_str = str(root)
    # Scope the path entry to this call: leaving it behind would hijack the
    # next `import app` elsewhere in the process.
    inserted = root_str not in sys.path
    if inserted:
        sys.path.insert(0, root_str)
    _evict_stale_app_modules(root)
    try:
        package = importlib.import_module("app.jobs")
        for module_info in pkgutil.iter_modules(package.__path__):
            if module_info.name.startswith("_"):
                continue
            importlib.import_module(f"app.jobs.{module_info.name}")
    except ModuleNotFoundError as exc:
        if exc.name not in ("app", "app.jobs"):
            raise
    finally:
        if inserted:
            sys.path.remove(root_str)
    return registered_jobs()


def _evict_stale_app_modules(root: Path) -> None:
    """Drop cached ``app``/``app.*`` modules bound to a different root.

    Without this, a previously imported project's ``app`` package shadows
    the one under ``root`` and its jobs silently win — and evicting only the
    two package markers leaves ``app.jobs.<module>`` submodules cached, which
    re-register the *first* root's handlers on the second import. Every
    ``app.*`` name is swept (mirroring ``fastplace.ai.vectors``). Containment
    is resolved-path based, not substring based, so a sibling root such as
    ``/work/a-v2`` never counts as ``/work/a``.
    """
    import sys

    evicted: set[str] = set()
    for name in list(sys.modules):
        if name != "app" and not name.startswith("app."):
            continue
        module = sys.modules.get(name)
        if module is None:
            continue
        origin = getattr(module, "__file__", None) or ""
        if not origin or not _path_contains(root, origin):
            sys.modules.pop(name, None)
            evicted.add(name)
    # Their @Job registrations died with the modules — drop those too, or the
    # fresh import collides on names the evicted handlers still "own".
    for job_name in list(registry):
        if registry[job_name].fn.__module__ in evicted:
            del registry[job_name]


def _path_contains(root: Path, origin: str) -> bool:
    """True when ``origin`` really lives under ``root`` (path containment)."""
    try:
        return Path(origin).resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False


class Job:
    """Decorator marking an async function as a queue handler.

    ``@Job()`` names the handler after the function; ``@Job(name="...")``
    pins a stable dotted name (use this when the function may be renamed or
    nested — dispatch names must stay constant across processes).
    """

    def __init__(self, name: str | None = None) -> None:
        self.name = name

    def __call__(self, fn: JobFn) -> JobFn:
        if not inspect.iscoroutinefunction(fn):
            raise ValueError(f"@Job handler '{getattr(fn, '__name__', fn)}' must be an async def")
        # Default to the plain function name — dispatch names must be stable
        # across processes, and __qualname__ would leak <locals> for handlers
        # defined inside factories. Pass name= to disambiguate collisions.
        name = self.name or fn.__name__
        if name in registry:
            existing = registry[name].fn
            raise ValueError(
                f"@Job name collision: '{name}' is already registered by "
                f"{getattr(existing, '__module__', '?')}.{getattr(existing, '__qualname__', '?')}; "
                f"{fn.__module__}.{fn.__qualname__} must pass @Job(name=...) to disambiguate"
            )
        registry[name] = _JobEntry(name=name, fn=fn)
        return fn


# ---------------------------------------------------------------------------
# drivers
# ---------------------------------------------------------------------------


@dataclass
class _Pending:
    """One queued invocation on the memory driver."""

    name: str
    kwargs: dict[str, Any]


@dataclass
class _Failure:
    """A handler exception captured while draining — isolated, never fatal."""

    name: str
    error: BaseException


class QueueDriver(Protocol):
    """The dispatch surface every driver implements."""

    async def dispatch(self, name: str, **kwargs: Any) -> None: ...

    async def clear(self) -> int:
        """Drop every pending job without running it; return how many."""
        ...

    async def queue_depth(self) -> int:
        """Waiting jobs — the backlog depth ``queue:monitor`` measures."""
        ...


class MemoryQueue:
    """In-process driver: dispatch validates + queues, run_pending drains."""

    def __init__(self) -> None:
        self.pending: deque[_Pending] = deque()
        self.failures: list[_Failure] = []

    async def dispatch(self, name: str, **kwargs: Any) -> None:
        if name not in registry:
            raise ValueError(f"unknown job '{name}' — is it registered with @Job?")
        self.pending.append(_Pending(name=name, kwargs=kwargs))

    async def run_pending(self, honor_sentinel: bool = True) -> int:
        """Execute the queued jobs; a failing handler is recorded, not raised.

        Only the batch present at entry is drained: a job whose side effect
        enqueues more work (a domain event → another job) chains to the *next*
        drain instead of looping this one forever. The failure ledger is
        scoped to the batch — a long-lived process draining repeatedly must
        not accumulate exception objects forever. Each failure is also
        persisted to the failed-job store (best-effort — a broken store never
        breaks the drain) for ``queue:failed`` / ``queue:retry``.

        A restart sentinel (:func:`restart_requested_at`) is checked *before*
        each job: when one is pending, the drain stops, the un-run jobs go
        back to the front of the deque for the replacement worker, and the
        sentinel is left untouched — consuming it is the exiting worker's
        job (the CLI does it for this driver). The sentinel carries no TTL —
        it stays latched until a worker consumes it — so a caller that will
        never consume it (the kernel's shutdown drain: the web process is
        not a restartable worker) must pass ``honor_sentinel=False`` or a
        single latched sentinel silently disables every drain it observes.
        """
        self.failures = []
        batch = list(self.pending)
        self.pending.clear()
        executed = 0
        for index, item in enumerate(batch):
            if honor_sentinel and await restart_requested_at() is not None:
                # extendleft(reversed(...)) keeps the block's dispatch order.
                self.pending.extendleft(reversed(batch[index:]))
                return executed
            try:
                await registry[item.name].fn(**item.kwargs)
            except Exception as exc:  # noqa: BLE001 — isolation is the contract
                self.failures.append(_Failure(name=item.name, error=exc))
                await record_failure(item.name, item.kwargs, format_error(exc))
            executed += 1
        return executed

    async def clear(self) -> int:
        """Discard the pending jobs without executing any; return the count.

        The failure ledger is left alone — it describes runs that already
        happened (see ``queue:failed``), not runs that never will.
        """
        cleared = len(self.pending)
        self.pending.clear()
        return cleared

    async def queue_depth(self) -> int:
        """Waiting jobs — the deque length ``queue:monitor`` measures."""
        return len(self.pending)


async def _record_saq_failure(ctx: dict[str, Any]) -> None:
    """``after_process`` hook — persist terminal saq failures to the store.

    Installed saq (0.26.4) has no ``Worker(on_failure=...)`` parameter; the
    after_process context is the failure signal: the worker sets
    ``ctx["exception"]`` on any processing error, then finishes the job FAILED
    or re-queues it depending on retryability — and after_process always runs
    afterwards with that ctx. Only terminal failures are recorded: a
    retryable attempt gets another chance first (its final failure, if any,
    is what lands here).
    """
    job = ctx.get("job")
    exc = ctx.get("exception")
    if job is None or exc is None:
        return
    from saq import Status

    if getattr(job, "status", None) != Status.FAILED:
        return
    await record_failure(str(job.function), dict(job.kwargs or {}), format_error(exc))


async def _check_restart_sentinel(ctx: dict[str, Any]) -> None:
    """``before_process`` hook — honor a restart request at the job boundary.

    This is the saq worker's sentinel poll (installed by
    :meth:`SaqQueue.build_worker`, so no caller can forget it). When a
    restart is pending: the already-dequeued job goes back via ``retry``
    (the replacement worker picks it up), the worker's stop event fires
    (in-flight jobs finish, no new ones start), the sentinel is consumed so
    the replacement doesn't exit again, and CancelledError stops the current
    job — installed saq's ``process()`` treats a cancellation raised before
    the job task exists as a clean skip, so the job is neither run nor
    failed. Caveat: the hook only fires per job — an idle worker honors the
    request when work next arrives, and with several workers sharing one
    sentinel the first to reach a boundary consumes it for everyone.
    """
    if await restart_requested_at() is None:
        return
    job = ctx.get("job")
    if job is not None:
        await job.retry("worker restart requested")
    worker = ctx.get("worker")
    if worker is not None:
        worker.event.set()
    await clear_restart_sentinel()
    raise asyncio.CancelledError


class SaqQueue:
    """Production driver on SAQ + redis — everything connects lazily.

    Tests inject ``queue=`` with a fake; a real RedisQueue (and its redis
    connection) is only built when dispatch or the worker actually runs.
    """

    def __init__(
        self,
        url: str | None = None,
        name: str | None = None,
        queue: Any | None = None,
    ) -> None:
        self._url = url or config("QUEUE_REDIS_URL", default="redis://localhost:6379/0")
        self._name = name or config("QUEUE_NAME", default="fastplace")
        self._queue = queue

    @property
    def queue(self) -> Any:
        if self._queue is None:
            import redis.asyncio as aioredis
            from saq.queue.redis import RedisQueue

            self._queue = RedisQueue(aioredis.from_url(self._url), name=self._name)
        return self._queue

    async def dispatch(self, name: str, **kwargs: Any) -> None:
        if name not in registry:
            raise ValueError(f"unknown job '{name}' — is it registered with @Job?")
        # One explicit kwargs dict: saq's enqueue hijacks any kwarg matching
        # its Job dataclass fields (timeout, ttl, kwargs…) into job
        # properties, which would silently drop handler arguments.
        await self.queue.enqueue(name, kwargs=kwargs)

    async def dispatch_delayed(
        self, name: str, kwargs: dict[str, Any] | None = None, delay: float = 1.0
    ) -> None:
        """Enqueue a named job for execution ``delay`` seconds from now.

        Installed saq (0.26.4) holds a job until its ``scheduled`` field — an
        absolute epoch-seconds timestamp, not a relative delay — comes due
        (the worker's schedule sweep promotes jobs with
        ``1 <= scheduled <= now``), so the due time is ``now + delay``; a
        small relative value would fall in the past and fire immediately.
        The Job is built explicitly rather than riding ``enqueue(kwargs=…)``
        because enqueue hijacks any kwarg matching its Job dataclass fields
        (timeout, ttl, kwargs…) into job properties, which would silently
        drop handler arguments. The method-local ``Job`` import shadows the
        @Job decorator on purpose — they share the name by saq's design.
        """
        from saq.job import Job

        if name not in registry:
            raise ValueError(f"unknown job '{name}' — is it registered with @Job?")
        job = Job(function=name, kwargs=dict(kwargs or {}), scheduled=int(time.time() + delay))
        await self.queue.enqueue(job)

    async def clear(self) -> int:
        """Delete queued and scheduled jobs; running jobs are left alone.

        Installed saq (0.26.4) has no ``flush()`` — the delete pattern below
        works off the queue's public redis surface. Job payloads live at
        their job-id keys and every unfinished id is tracked in the
        ``incomplete`` sorted set; ids sitting in the ``active`` list belong
        to jobs a worker is running right now, so those (and their
        bookkeeping) stay untouched.
        """
        q = self.queue
        redis = q.redis
        active = set(await redis.lrange(q.namespace("active"), 0, -1))
        tracked = await redis.zrange(q.namespace("incomplete"), 0, -1)
        stale = [job_id for job_id in tracked if job_id not in active]
        async with redis.pipeline(transaction=True) as pipe:
            for job_id in stale:
                pipe.delete(job_id)
            if stale:
                pipe.zrem(q.namespace("incomplete"), *stale)
            pipe.delete(q.namespace("queued"))
            await pipe.execute()
        return len(stale)

    async def queue_depth(self) -> int:
        """Waiting jobs — installed saq (0.26.4) has no ``stats()``; the
        public ``count("queued")`` (one llen of the queue list) is the
        backlog ``queue:monitor`` measures. Active jobs are being processed,
        not waiting, so they stay out of the depth.
        """
        return int(await self.queue.count("queued"))

    def build_worker(self, **kwargs: Any) -> Any:
        """Assemble a saq Worker over the registry (no network until start).

        The worker always carries the restart-checking before_process hook
        (see :func:`_check_restart_sentinel`) and the failure-recording
        after_process hook (see :func:`_record_saq_failure`); caller kwargs
        pass through untouched, and a caller-supplied ``before_process`` is
        merged to run after the built-in, so the CLI's translated options
        keep working.
        """
        from saq import Worker

        functions = [(entry.name, entry.fn) for entry in registry.values()]
        hooks = [_check_restart_sentinel]
        caller_hooks = kwargs.pop("before_process", None)
        if caller_hooks is not None:
            # saq accepts a single callable or a collection — normalize to
            # the collection form and append.
            hooks.extend(caller_hooks if isinstance(caller_hooks, Collection) else [caller_hooks])
        return Worker(
            self.queue,
            functions=functions,
            before_process=hooks,
            after_process=_record_saq_failure,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# restart sentinel — the cross-process "exit at your next job boundary" flag
# ---------------------------------------------------------------------------

#: Cache-store key carrying the restart sentinel. The value is a JSON-safe
#: ISO timestamp (naive UTC, the ledger's convention) so every cache driver
#: round-trips it; the timestamp records *when* the restart was asked for.
RESTART_SENTINEL_KEY = "fastplace:queue:restart"


async def set_restart_sentinel() -> None:
    """Publish the restart sentinel (``queue:restart``).

    Strict on purpose: if the store cannot be written, the command must
    fail rather than pretend workers were asked to restart.
    """
    from fastplace.cache import cache

    await cache().put(RESTART_SENTINEL_KEY, utcnow().isoformat())


async def restart_requested_at() -> datetime | None:
    """When a restart was requested, or ``None`` when none is pending.

    Best-effort like the drain's other dependencies: a cache outage or a
    corrupt value reads as "no restart" (workers keep working — the
    availability-safe direction) instead of taking the drain down or
    exit-looping every worker.
    """
    from fastplace.cache import cache

    try:
        raw = await cache().get(RESTART_SENTINEL_KEY)
    except Exception:  # noqa: BLE001 — the restart protocol is best-effort
        logger.warning("restart sentinel read failed — treating as no restart", exc_info=True)
        return None
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        logger.warning("ignoring corrupt restart sentinel value %r", raw)
        return None


async def clear_restart_sentinel() -> None:
    """Consume the sentinel — the worker honoring it calls this as it exits,
    so the replacement worker does not immediately exit again.

    Best-effort with a log: the caller is on its way out (exit code 0 per
    the restart contract), and a raise here would only turn a graceful
    exit into a traceback.
    """
    from fastplace.cache import cache

    try:
        await cache().forget(RESTART_SENTINEL_KEY)
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning(
            "restart sentinel clear failed — a replacement may exit again", exc_info=True
        )


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------

_default_queue: QueueDriver | None = None


def queue() -> QueueDriver:
    """The process-wide queue (``QUEUE_DRIVER``, default ``memory``)."""
    global _default_queue
    if _default_queue is None:
        driver = config("QUEUE_DRIVER", default="memory")
        if driver == "memory":
            _default_queue = MemoryQueue()
        elif driver == "saq":
            _default_queue = SaqQueue()
        else:
            raise ConfigurationError(f"unknown QUEUE_DRIVER '{driver}'")
    return _default_queue


def reset_queue() -> None:
    """Drop the singleton — tests and config reloads."""
    global _default_queue
    _default_queue = None


def set_queue(driver: QueueDriver) -> None:
    """Install a custom driver as the process-wide queue.

    The hook wrapper packages (fastplace-tenancy's ``TenantQueue``) need:
    everything that dispatches through :func:`queue` — application code and
    the domain-event bridge alike — then flows through the wrapper.
    """
    global _default_queue
    _default_queue = driver
