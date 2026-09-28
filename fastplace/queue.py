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

Composition patterns
--------------------

* **Batch fan-out** — ``await queue().dispatch_many([("job", {...}), ...])``
  enqueues a whole list in one call: names are validated atomically (one
  unknown name refuses the batch before anything queues) and handles come
  back in input order.
* **Unique dispatches** — ``queue().job(name, unique=True).dispatch(**kw)``
  gives the job a deterministic identity; an identical unresolved dispatch
  is suppressed on both drivers (``dispatch()`` returns ``None`` then).
* **Mutual exclusion** (the "locked job" idiom) — claim an atomic cache
  counter around the body, release it in a ``finally``; the TTL is the
  crash safety net (a worker that dies mid-job never reaches the release)::

      if await cache().increment(f"lock:nightly_sync", ttl=1800) != 1:
          return  # another run holds the lock — skip, not queue
      try:
          ...  # the guarded body
      finally:
          await cache().forget("lock:nightly_sync")

  Cross-process locking needs the redis cache driver (its ``increment`` is
  a single atomic ``INCR``); the database driver reads the counter back in
  a second transaction, so two concurrent writers can observe the same
  count and both believe they hold the lock.
* **Chaining and rate limiting** — deliberately not APIs yet (deferred
  until real demand; a Chain builder that cannot express failure semantics
  is worse than the explicit idiom). Interim idioms: a chain is a handler
  whose last line dispatches the next job (normal completion IS the
  "previous step succeeded" signal; a failed step records to the
  failed-job ledger and never enqueues its successor), and a rate limit is
  a cache token bucket at the top of the handler::

      if await cache().increment(f"rl:fetch_prices", ttl=60) > 120:
          return  # over budget for this window — skip or re-dispatch delayed
"""

from __future__ import annotations

import asyncio
import functools
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
from fastplace.logging import job_context
from fastplace.queue_failures import format_error, record_failure, utcnow

logger = logging.getLogger("fastplace.queue")

#: A job handler — always async; kwargs arrive from dispatch.
JobFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class _JobEntry:
    """One registered handler under its dispatch name.

    ``retries``/``timeout``/``backoff`` are the handler's reliability
    envelope preferences (``@Job`` params) — ``None`` means "no opinion",
    letting the env defaults or a per-dispatch builder override decide.
    """

    name: str
    fn: JobFn
    retries: int | None = None
    timeout: float | None = None
    backoff: float | None = None


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

    The optional reliability params pin this handler's retry envelope —
    ``retries`` (total attempts, >= 1), ``timeout`` (per-attempt seconds),
    ``backoff`` (exponential base delay in seconds between attempts). They
    sit between the ``QUEUE_*`` env defaults and a per-dispatch builder
    override: builder > ``@Job`` > env > framework default.
    """

    def __init__(
        self,
        name: str | None = None,
        *,
        retries: int | None = None,
        timeout: float | None = None,
        backoff: float | None = None,
    ) -> None:
        _validate_envelope_params(retries=retries, timeout=timeout, backoff=backoff)
        self.name = name
        self.retries = retries
        self.timeout = timeout
        self.backoff = backoff

    def __call__(self, fn: JobFn) -> JobFn:
        if not inspect.iscoroutinefunction(fn):
            raise ValueError(f"@Job handler '{getattr(fn, '__name__', fn)}' must be an async def")
        # Default to the plain function name — dispatch names must be stable
        # across processes, and __qualname__ would leak <locals> for handlers
        # defined inside factories. Pass name= to disambiguate collisions.
        name = self.name or fn.__name__
        entry = _JobEntry(
            name=name, fn=fn, retries=self.retries, timeout=self.timeout, backoff=self.backoff
        )
        if name in registry:
            existing = registry[name].fn
            if _is_stale_registration(existing) and _same_dotted_path(existing, fn):
                # Module-reload semantics: the old handler's module was
                # evicted from sys.modules (boot sandboxes evict app.* and
                # may re-import app.jobs within one boot), so this is the
                # same handler re-registered by a fresh module object —
                # replace the dead entry rather than raise.
                registry[name] = entry
                return fn
            raise ValueError(
                f"@Job name collision: '{name}' is already registered by "
                f"{getattr(existing, '__module__', '?')}.{getattr(existing, '__qualname__', '?')}; "
                f"{fn.__module__}.{fn.__qualname__} must pass @Job(name=...) to disambiguate"
            )
        registry[name] = entry
        return fn


def _is_stale_registration(existing: JobFn) -> bool:
    """True when the existing handler outlived its module's namespace.

    A decorator only re-runs for a freshly executed module, and Python seats
    that module in sys.modules before its body runs — so "module absent"
    can't spot the eviction. The reliable signal is namespace identity: the
    existing fn's ``__globals__`` is the namespace it was defined in, and
    when the cached module's ``__dict__`` is a DIFFERENT object the old
    namespace was orphaned by an eviction+re-import. A live module's
    redefinition (same namespace) stays a genuine collision. (True
    ``importlib.reload`` reuses one namespace and still raises — no code
    path reloads job modules; evict-and-reimport is the supported flow.)"""
    import sys

    module_name = getattr(existing, "__module__", "") or ""
    module = sys.modules.get(module_name)
    if module is None:
        return True  # the whole module is gone — orphaned
    return module.__dict__ is not existing.__globals__


def _same_dotted_path(a: JobFn, b: JobFn) -> bool:
    """True when two function objects claim the identical module + qualname."""
    return (a.__module__, a.__qualname__) == (b.__module__, b.__qualname__)


# ---------------------------------------------------------------------------
# drivers
# ---------------------------------------------------------------------------


@dataclass
class _Pending:
    """One queued invocation on the memory driver."""

    name: str
    kwargs: dict[str, Any]
    key: str = ""
    options: dict[str, Any] | None = None
    scheduled_at: float = 0.0  # epoch seconds; 0 = due immediately


@dataclass
class _MemoryJobHandle:
    """The in-process handle a memory dispatch returns (q1-G10)."""

    key: str
    name: str
    status: str = "queued"  # queued → completed | failed | discarded


@dataclass
class _Failure:
    """A handler exception captured while draining — isolated, never fatal."""

    name: str
    error: BaseException


class QueueDriver(Protocol):
    """The dispatch surface every driver implements."""

    async def dispatch(self, name: str, **kwargs: Any) -> Any: ...

    async def dispatch_many(self, jobs: list[tuple[str, dict[str, Any]]]) -> list[Any]:
        """Enqueue a batch of ``(name, kwargs)`` dispatches in one call.

        Validation is atomic: one unknown name refuses the whole batch with
        ``ValueError`` before anything is enqueued. Returns the handles in
        input order (q1-G6 batching).
        """
        ...

    async def clear(self) -> int:
        """Drop every pending job without running it; return how many."""
        ...

    async def queue_depth(self) -> int:
        """Waiting jobs — the backlog depth ``queue:monitor`` measures."""
        ...

    def job(self, name: str, **options: Any) -> PendingDispatch:
        """Start a dispatch with reliability options (the builder entry)."""
        ...

    async def job_status(self, key: str, queue: str | None = None) -> str | None:
        """Status of a dispatched job handle's key, None when unknown.

        ``queue=`` names the queue a routed dispatch landed on (drivers with
        named queues; a no-op where there is only one).
        """
        ...

    async def _dispatch_pending(self, pending: PendingDispatch, kwargs: dict[str, Any]) -> Any:
        """Complete a builder dispatch — every driver implements this so
        :class:`PendingDispatch` can hand off type-safely (internal)."""
        ...


class MemoryQueue:
    """In-process driver: dispatch validates + queues, run_pending drains."""

    #: How many dispatch handles ``job_status`` keeps — a recent window, not
    #: an unbounded ledger. A long-lived dev process churning thousands of
    #: jobs must not pin every handle forever; status polls are about recent
    #: dispatches (the same bound the saq driver's route memory carries).
    _DISPATCHED_CAP: int = 1024

    def __init__(self) -> None:
        self.pending: deque[_Pending] = deque()
        self.failures: list[_Failure] = []
        self.dispatched: dict[str, _MemoryJobHandle] = {}

    async def dispatch(self, name: str, **kwargs: Any) -> _MemoryJobHandle | None:
        if name not in registry:
            raise ValueError(f"unknown job '{name}' — is it registered with @Job?")
        return await self._enqueue_memory(name, kwargs, _resolve_envelope(registry[name], {}))

    async def dispatch_many(
        self, jobs: list[tuple[str, dict[str, Any]]]
    ) -> list[_MemoryJobHandle | None]:
        """Enqueue a whole batch of plain dispatches; handles in input order.

        See :meth:`QueueDriver.dispatch_many` for the contract (atomic
        validation, preserved order).
        """
        _validate_batch_names([name for name, _ in jobs])
        return [
            await self._enqueue_memory(name, kwargs, _resolve_envelope(registry[name], {}))
            for name, kwargs in jobs
        ]

    def job(self, name: str, **options: Any) -> PendingDispatch:
        """Builder entry — see :class:`PendingDispatch` (rejects ``queue=``)."""
        return PendingDispatch(self, name, **options)

    async def _dispatch_pending(
        self, pending: PendingDispatch, kwargs: dict[str, Any]
    ) -> _MemoryJobHandle | None:
        if pending.name not in registry:
            raise ValueError(f"unknown job '{pending.name}' — is it registered with @Job?")
        if pending.queue_name is not None:
            raise ValueError(
                "the memory driver has a single in-process queue — "
                "queue routing needs the saq driver"
            )
        overrides = {
            "retries": pending.retries,
            "timeout": pending.timeout,
            "backoff": pending.backoff,
            "ttl": pending.ttl,  # accepted for API symmetry; nothing to retain
        }
        envelope = _resolve_envelope(registry[pending.name], overrides)
        return await self._enqueue_memory(
            pending.name,
            kwargs,
            envelope,
            delay=pending.delay or 0.0,
            unique=pending.unique,
        )

    async def _enqueue_memory(
        self,
        name: str,
        kwargs: dict[str, Any],
        envelope: dict[str, Any],
        delay: float = 0.0,
        unique: bool = False,
    ) -> _MemoryJobHandle | None:
        import uuid

        key = _deterministic_job_key(name, kwargs) if unique else uuid.uuid4().hex
        if unique:
            existing = self.dispatched.get(key)
            if existing is not None and existing.status == "queued":
                return None  # identical unresolved job already queued
        handle = _MemoryJobHandle(key=key, name=name)
        self.dispatched[key] = handle
        if len(self.dispatched) > self._DISPATCHED_CAP:
            # FIFO-evict past the window (dict insertion order = arrival
            # order); an evicted key's job_status reads as unknown.
            self.dispatched.pop(next(iter(self.dispatched)))
        self.pending.append(
            _Pending(
                name=name,
                kwargs=kwargs,
                key=key,
                options=envelope,
                scheduled_at=(time.time() + delay) if delay else 0.0,
            )
        )
        return handle

    async def run_pending(self, honor_sentinel: bool = True) -> int:
        """Execute the queued jobs; a failing handler is recorded, not raised.

        Each job runs under its dispatch envelope: ``timeout`` bounds every
        attempt, ``retries`` sets the total attempt budget, ``backoff`` waits
        ``base * 2^(N-1)`` seconds after the Nth failed attempt — the same
        envelope a saq worker applies, so dev drains behave like production.
        A job whose ``scheduled_at`` is still in the future is deferred back
        to the front of the deque (a delayed dispatch survives until due).

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
        deferred: list[_Pending] = []
        for index, item in enumerate(batch):
            if item.scheduled_at and item.scheduled_at > time.time():
                deferred.append(item)
                continue
            if honor_sentinel and await restart_requested_at() is not None:
                # extendleft(reversed(...)) keeps the block's dispatch order;
                # deferred items come first — they were earlier in the batch.
                remaining = deferred + batch[index:]
                self.pending.extendleft(reversed(remaining))
                return executed
            await self._run_one(item)
            executed += 1
        if deferred:
            self.pending.extendleft(reversed(deferred))
        return executed

    async def _run_one(self, item: _Pending) -> None:
        """One job under its envelope — attempts, timeout, backoff, ledger."""
        envelope = item.options or _resolve_envelope(registry.get(item.name), {})
        handle = self.dispatched.get(item.key) if item.key else None
        attempts = 0
        while True:
            attempts += 1
            try:
                # plat-G9 correlation: log records from inside the handler
                # carry the job name as job_id, same as a saq worker does.
                with job_context(item.name):
                    await asyncio.wait_for(
                        registry[item.name].fn(**item.kwargs), timeout=envelope["timeout"]
                    )
                if handle is not None:
                    handle.status = "completed"
                return
            except Exception as exc:  # noqa: BLE001 — isolation is the contract
                if attempts < envelope["retries"]:
                    delay = _backoff_delay(envelope["backoff"], attempts)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue
                self.failures.append(_Failure(name=item.name, error=exc))
                await record_failure(item.name, item.kwargs, format_error(exc))
                if handle is not None:
                    handle.status = "failed"
                return

    async def clear(self) -> int:
        """Discard the pending jobs without executing any; return the count.

        The failure ledger is left alone — it describes runs that already
        happened (see ``queue:failed``), not runs that never will. A cleared
        job's handle flips to ``discarded`` so a ``unique=`` identity it
        held is freed for the next dispatch.
        """
        cleared = len(self.pending)
        for item in self.pending:
            handle = self.dispatched.get(item.key) if item.key else None
            if handle is not None and handle.status == "queued":
                handle.status = "discarded"
        self.pending.clear()
        return cleared

    async def queue_depth(self) -> int:
        """Waiting jobs — the deque length ``queue:monitor`` measures."""
        return len(self.pending)

    async def job_status(self, key: str, queue: str | None = None) -> str | None:
        """The in-process handle's status, None when no such dispatch.

        ``queue=`` is accepted for protocol parity and ignored — one
        in-process queue holds every dispatch.
        """
        handle = self.dispatched.get(key)
        return handle.status if handle is not None else None


def _validate_batch_names(names: list[str]) -> None:
    """Refuse a batch naming any unregistered job — before anything enqueues.

    ``dispatch_many``'s contract is all-or-nothing validation: looping
    ``dispatch()`` by hand lets item 3 of 5 fail after items 1-2 already
    queued, leaving a half-applied batch behind. ``dict.fromkeys`` keeps the
    unknown names in first-seen order without rescanning the list per name.
    """
    unknown = [name for name in dict.fromkeys(names) if name not in registry]
    if unknown:
        listed = ", ".join(f"'{name}'" for name in unknown)
        raise ValueError(
            f"unknown job(s) in batch: {listed} — is it registered with @Job? "
            "Nothing from this batch was enqueued."
        )


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
    """``after_process`` hook — honor a restart request at the job boundary.

    This is the saq worker's sentinel poll (installed by
    :meth:`SaqQueue.build_worker`, so no caller can forget it). It runs
    AFTER the job's task finished — that placement is the exactly-once
    guarantee: a ``before_process`` variant had no way to stop the worker
    without cancelling the job it fired for (saq's stop() cancels in-flight
    tasks once the event is set, and a pre-task CancelledError from
    before_process drops the job back to ACTIVE for the sweeper to abort
    ~90s later — the silent restart-loss path). When a restart is pending:
    the sentinel is consumed first (so the exit is verifiably clean), then
    the worker's stop event fires — the finished job ran exactly once and
    no new ones start. A failure to clear the sentinel is logged and left
    latched; the CLI's exit path turns a surviving sentinel into a
    non-zero exit. Caveat: the hook fires per completed job — an idle
    worker honors the request when work next arrives, jobs dequeued into
    other concurrency slots before the first post-job check still run
    (they were in flight), and with several workers sharing one sentinel
    the first to reach a boundary consumes it for everyone (restart the
    fleet with a per-process supervisor, e.g. systemd, instead of relying
    on one sentinel retiring every worker).
    """
    if await restart_requested_at() is None:
        return
    try:
        await clear_restart_sentinel()
    except Exception:  # noqa: BLE001 — the completed job stays completed
        logger.error(
            "restart sentinel clear failed — sentinel stays latched; "
            "the worker's exit path must report the failed restart",
            exc_info=True,
        )
    worker = ctx.get("worker")
    if worker is not None:
        worker.event.set()


# ---------------------------------------------------------------------------
# the reliability envelope — retries/timeout/backoff/ttl on every dispatch
# ---------------------------------------------------------------------------


def _validate_envelope_params(
    retries: int | None = None,
    timeout: float | None = None,
    backoff: float | None = None,
    ttl: int | None = None,
    delay: float | None = None,
) -> None:
    """Refuse nonsense envelope values at the boundary where they enter.

    ``retries`` is total attempts (>= 1), ``timeout`` per-attempt seconds
    (> 0), ``backoff`` the exponential base delay in seconds (>= 0, 0 = off),
    ``ttl`` result retention seconds (>= 1), ``delay`` seconds until the job
    becomes due (>= 0).
    """
    if retries is not None and retries < 1:
        raise ValueError(f"retries must be >= 1 (total attempts), got {retries}")
    if timeout is not None and timeout <= 0:
        raise ValueError(f"timeout must be > 0 seconds, got {timeout}")
    if backoff is not None and backoff < 0:
        raise ValueError(f"backoff must be >= 0 seconds, got {backoff}")
    if ttl is not None and ttl < 1:
        raise ValueError(f"ttl must be >= 1 second, got {ttl}")
    if delay is not None and delay < 0:
        raise ValueError(f"delay must be >= 0 seconds, got {delay}")


def _envelope_defaults() -> dict[str, Any]:
    """Framework defaults from ``QUEUE_*`` env — the envelope's floor layer.

    Defaults: ``QUEUE_TRIES=3`` (a job swept up by a worker crash stays
    retryable), ``QUEUE_TIMEOUT=60`` (replaces saq's hidden 10s dataclass
    default), ``QUEUE_BACKOFF=0`` (off), ``QUEUE_TTL=600``. Misconfigured
    values fail loudly at dispatch — a zero timeout would cut every job
    instantly while looking configured.
    """
    tries = int(config("QUEUE_TRIES", default=3))
    timeout = float(config("QUEUE_TIMEOUT", default=60.0))
    backoff = float(config("QUEUE_BACKOFF", default=0.0))
    ttl = int(config("QUEUE_TTL", default=600))
    for key, value in (
        ("QUEUE_TRIES", tries),
        ("QUEUE_TIMEOUT", timeout),
        ("QUEUE_BACKOFF", backoff),
        ("QUEUE_TTL", ttl),
    ):
        if key == "QUEUE_TRIES" and value < 1:
            raise ConfigurationError(f"{key} must be >= 1, got {value!r}")
        if key != "QUEUE_TRIES" and value < 0:
            raise ConfigurationError(f"{key} must be >= 0, got {value!r}")
        if key == "QUEUE_TIMEOUT" and value == 0:
            raise ConfigurationError(f"{key} must be > 0, got {value!r}")
        if key == "QUEUE_TTL" and value < 1:
            # Zero retention is a misconfiguration, not a policy — saq would
            # take ttl=0 and keep the result forever while looking configured.
            raise ConfigurationError(f"{key} must be >= 1, got {value!r}")
    return {"retries": tries, "timeout": timeout, "backoff": backoff, "ttl": ttl}


def _resolve_envelope(entry: _JobEntry | None, overrides: dict[str, Any]) -> dict[str, Any]:
    """Merge the envelope's layers: env defaults < @Job params < builder.

    ``None`` at a layer means "no opinion" — the layer below decides.
    """
    resolved = _envelope_defaults()
    if entry is not None:
        if entry.retries is not None:
            resolved["retries"] = entry.retries
        if entry.timeout is not None:
            resolved["timeout"] = entry.timeout
        if entry.backoff is not None:
            resolved["backoff"] = entry.backoff
    for key, value in overrides.items():
        if value is not None:
            resolved[key] = value
    return resolved


def effective_job_options(entry: _JobEntry | None = None) -> dict[str, Any]:
    """The envelope a dispatch of ``entry`` would carry (queue:list/health).

    Public on purpose: the CLI surfaces these numbers so a team can see the
    effective envelope without decoding three layers by hand.
    """
    return _resolve_envelope(entry, {})


def _deterministic_job_key(name: str, kwargs: dict[str, Any]) -> str:
    """Stable identity for a unique dispatch: same name + same kwargs."""
    import hashlib
    import json

    payload = json.dumps(kwargs, sort_keys=True, default=str)
    digest = hashlib.sha1(f"{name}|{payload}".encode()).hexdigest()[:16]
    return f"{name}:{digest}"


def _backoff_delay(backoff: float, failed_attempts: int) -> float:
    """Exponential delay after the Nth failed attempt: ``base * 2^(N-1)``.

    Mirrors what saq computes from ``retry_delay=base, retry_backoff=True``
    (its ``exponential_backoff``), without the jitter — the memory driver
    stays deterministic so drains are testable.
    """
    if not backoff:
        return 0.0
    return float(backoff) * 2 ** max(failed_attempts - 1, 0)


class PendingDispatch:
    """A dispatch under construction — ``queue().job(name, **options)``.

    Options live here instead of on ``dispatch()`` because handler kwargs
    ride dispatch (a handler may legitimately take ``timeout=`` as its own
    argument) — the two namespaces must never collide::

        await queue().job("welcome_email", retries=5, backoff=2).dispatch(user_id=7)

    ``retries``/``timeout``/``backoff``/``ttl`` shape the reliability envelope
    (precedence: builder > ``@Job`` > ``QUEUE_*`` env > default); ``delay``
    holds the job until now+delay seconds; ``queue=`` routes to a named saq
    queue (workers opt in via ``queue:work --queue``); ``unique=True`` gives
    the dispatch a deterministic identity — an identical unresolved dispatch
    is suppressed (both drivers), and ``dispatch()`` returns ``None`` when
    that suppression fires. ``dispatch()`` returns a trackable handle (the
    saq Job on redis, an in-process handle on memory). The memory driver
    applies retries/timeout/backoff/delay/unique; ``ttl`` and ``queue`` are
    redis-queue concepts it cannot honor, so it refuses ``queue=`` and
    ignores ``ttl``.
    """

    def __init__(
        self,
        driver: QueueDriver,
        name: str,
        *,
        retries: int | None = None,
        timeout: float | None = None,
        backoff: float | None = None,
        ttl: int | None = None,
        delay: float | None = None,
        queue: str | None = None,
        unique: bool = False,
    ) -> None:
        _validate_envelope_params(
            retries=retries, timeout=timeout, backoff=backoff, ttl=ttl, delay=delay
        )
        self._driver = driver
        self.name = name
        self.retries = retries
        self.timeout = timeout
        self.backoff = backoff
        self.ttl = ttl
        self.delay = delay
        self.queue_name = queue
        self.unique = unique

    async def dispatch(self, **kwargs: Any) -> Any:
        """Enqueue the job; returns its handle, or ``None`` when a unique
        dispatch found an identical unresolved job already queued."""
        return await self._driver._dispatch_pending(self, kwargs)  # noqa: SLF001


def _adapt_sa_handler(fn: JobFn) -> JobFn:
    """Wrap a ``@Job`` handler for saq's calling convention.

    saq's ``Worker.process()`` invokes every registered function as
    ``function(context, **kwargs)`` — the ctx dict rides as a leading
    positional. The documented ``@Job`` shape is ``fn(**kwargs)`` with no
    context parameter, so registering handlers bare breaks production
    dispatch two ways: a kwarg-carrying job dies with ``TypeError: got
    multiple values for argument``, and a no-kwarg job silently receives
    the ctx dict as its first declared parameter. The shim absorbs the
    positional and forwards only the dispatched kwargs. ``functools.wraps``
    keeps the handler's name/doc visible in saq's logs. It is also the one
    seam every production job passes, so it scopes the job's log correlation
    (``job_context``) — records from inside the handler carry its name.
    """

    @functools.wraps(fn)
    async def _wrapped(_ctx: dict[str, Any], **kwargs: Any) -> Any:
        with job_context(str(_ctx.get("name") or fn.__name__)):
            return await fn(**kwargs)

    return _wrapped  # type: ignore[return-value]


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
        # Named-queue routing targets (q1-G4): ``queue().job(..., queue="emails")``
        # enqueues on a RedisQueue with that name; workers opt in per name.
        self._routes: dict[str, Any] = {}
        # Where each routed key landed (bounded memory): ``job_status`` needs
        # it to poll the right queue — the handle's key is invisible on the
        # default queue otherwise.
        self._key_routes: dict[str, str] = {}

    @property
    def queue(self) -> Any:
        if self._queue is None:
            import redis.asyncio as aioredis
            from saq.queue.redis import RedisQueue

            self._queue = RedisQueue(aioredis.from_url(self._url), name=self._name)
        return self._queue

    def _queue_for(self, name: str | None) -> Any:
        """The queue a dispatch under ``name`` belongs to (routing cache)."""
        if name is None or name == self._name:
            return self.queue
        if name not in self._routes:
            import redis.asyncio as aioredis
            from saq.queue.redis import RedisQueue

            self._routes[name] = RedisQueue(aioredis.from_url(self._url), name=name)
        return self._routes[name]

    async def dispatch(self, name: str, **kwargs: Any) -> Any:
        if name not in registry:
            raise ValueError(f"unknown job '{name}' — is it registered with @Job?")
        return await self._enqueue_saq(name, kwargs, _resolve_envelope(registry[name], {}))

    async def dispatch_many(self, jobs: list[tuple[str, dict[str, Any]]]) -> list[Any]:
        """Enqueue a whole batch of plain dispatches; handles in input order.

        See :meth:`QueueDriver.dispatch_many` for the contract (atomic
        validation, preserved order). Per-item envelope options are not part
        of the batch surface: build those dispatches with
        :meth:`job` individually — a batch is for uniform fan-out.
        """
        _validate_batch_names([name for name, _ in jobs])
        return [
            await self._enqueue_saq(name, kwargs, _resolve_envelope(registry[name], {}))
            for name, kwargs in jobs
        ]

    def job(self, name: str, **options: Any) -> PendingDispatch:
        """Builder entry — see :class:`PendingDispatch`."""
        return PendingDispatch(self, name, **options)

    async def _dispatch_pending(self, pending: PendingDispatch, kwargs: dict[str, Any]) -> Any:
        if pending.name not in registry:
            raise ValueError(f"unknown job '{pending.name}' — is it registered with @Job?")
        overrides = {
            "retries": pending.retries,
            "timeout": pending.timeout,
            "backoff": pending.backoff,
            "ttl": pending.ttl,
        }
        envelope = _resolve_envelope(registry[pending.name], overrides)
        return await self._enqueue_saq(
            pending.name,
            kwargs,
            envelope,
            delay=pending.delay or 0.0,
            queue_name=pending.queue_name,
            unique=pending.unique,
        )

    async def _enqueue_saq(
        self,
        name: str,
        kwargs: dict[str, Any],
        envelope: dict[str, Any],
        delay: float = 0.0,
        queue_name: str | None = None,
        unique: bool = False,
    ) -> Any:
        """Enqueue one explicit saq Job carrying the resolved envelope.

        The Job is built explicitly (never ``enqueue(name, **kwargs)``) for
        two reasons: saq hijacks any kwarg matching its Job dataclass fields
        (timeout, ttl, kwargs…) into job properties, silently dropping
        handler arguments; and the envelope's fields must be set
        deliberately — saq's hidden dataclass defaults (timeout=10,
        retries=1) are not this framework's defaults. ``retry_delay`` is the
        exponential base and ``retry_backoff`` the max cap in saq's formula,
        so ``backoff=X`` seconds becomes ``retry_delay=X, retry_backoff=True``
        (unbounded cap — the base carries the policy). Returns whatever
        enqueue returns: the Job, or ``None`` when a ``unique=`` key was
        already queued (saq's duplicate suppression).
        """
        import uuid

        from saq.job import Job  # shadows the @Job decorator by saq's design

        backoff = envelope["backoff"] or 0.0
        job = Job(
            function=name,
            kwargs=dict(kwargs),
            retries=envelope["retries"],
            # saq annotates timeout as int but honors floats at runtime
            # (asyncio.wait_for takes one) — fractional timeouts are valid.
            timeout=float(envelope["timeout"]),  # type: ignore[arg-type]
            ttl=envelope["ttl"],
            retry_delay=backoff,
            retry_backoff=True if backoff else False,
            scheduled=int(time.time() + delay) if delay else 0,
            key=_deterministic_job_key(name, kwargs) if unique else uuid.uuid4().hex,
        )
        if queue_name is not None and queue_name != self._name:
            # Remember the landing spot so job_status can poll the right
            # queue; bounded — a status poll is about recent dispatches.
            self._key_routes[job.key] = queue_name
            if len(self._key_routes) > 1024:
                self._key_routes.pop(next(iter(self._key_routes)))
        return await self._queue_for(queue_name).enqueue(job)

    async def dispatch_delayed(
        self, name: str, kwargs: dict[str, Any] | None = None, delay: float = 1.0
    ) -> Any:
        """Enqueue a named job for execution ``delay`` seconds from now.

        Installed saq (0.26.4) holds a job until its ``scheduled`` field — an
        absolute epoch-seconds timestamp, not a relative delay — comes due
        (the worker's schedule sweep promotes jobs with
        ``1 <= scheduled <= now``), so the due time is ``now + delay``; a
        small relative value would fall in the past and fire immediately.
        The job carries the standard resolved envelope; returns the enqueued
        Job (the handle a caller can poll).
        """
        if name not in registry:
            raise ValueError(f"unknown job '{name}' — is it registered with @Job?")
        return await self._enqueue_saq(
            name, dict(kwargs or {}), _resolve_envelope(registry[name], {}), delay=delay
        )

    async def job_status(self, key: str, queue: str | None = None) -> str | None:
        """The saq Job's status for ``key`` (its job id), None when unknown.

        A routed dispatch's handle lives on its named queue, invisible to a
        default-queue poll — so ``queue=`` names it explicitly, and without
        the hint the driver consults where it routed the key, then the
        default queue, then every named queue this process has routed to.
        """
        target = queue or self._key_routes.get(key)
        queues = (
            [self._queue_for(target)]
            if target is not None
            else [self.queue, *self._routes.values()]
        )
        for candidate in queues:
            job = await candidate.job(key)
            if job is not None:
                status = getattr(job, "status", None)
                return str(getattr(status, "value", status))
        return None

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

    async def active_depth(self) -> int:
        """Jobs currently being processed (saq ``count("active")``).

        Reported by ``queue:monitor`` beside the depth: a worker that died
        mid-job leaves its jobs stuck ACTIVE — the visible crash symptom in
        the window before saq's sweeper aborts them (~90s).
        """
        return int(await self.queue.count("active"))

    async def aborted_jobs(self) -> list[Any]:
        """Enumerate jobs saq left ABORTED — the crash-loss record.

        A worker death mid-job ends with the sweeper aborting the job
        (status ABORTED, error ``swept``) inside redis, where nothing human
        browses. ``iter_jobs`` (a SCAN over job keys filtered by status) is
        the enumeration primitive installed saq offers; ABORTED is terminal,
        so the same job appears here on every pass until its TTL expires —
        dedupe lives in :meth:`record_aborted_jobs`.
        """
        from saq.job import Status

        return [job async for job in self.queue.iter_jobs(statuses=[Status.ABORTED])]

    async def record_aborted_jobs(self) -> list[str]:
        """Ledger every ABORTED job exactly once; returns the names recorded.

        All aborted jobs are recorded — visibility over silence — the error
        text distinguishes a swept crash-loss (``swept``) from an explicit
        cancel. Deduping on the saq job key keeps repeated passes (and
        several workers scanning concurrently) from double-ledgering one
        job. Best-effort by design: a store outage logs and returns what it
        managed, because this runs beside a live worker loop.
        """
        from fastplace.queue_failures import record_failure_once

        recorded: list[str] = []
        for job in await self.aborted_jobs():
            error = str(getattr(job, "error", None) or "aborted")
            job_key = str(getattr(job, "key", "") or "")
            recorded_id = await record_failure_once(
                str(job.function), dict(job.kwargs or {}), f"aborted: {error}", job_key
            )
            if recorded_id is not None:
                recorded.append(str(job.function))
        return recorded

    def build_worker(self, **kwargs: Any) -> Any:
        """Assemble a saq Worker over the registry (no network until start).

        Every handler is adapted to saq's ``fn(ctx, **kwargs)`` calling
        convention (see :func:`_adapt_sa_handler`). The worker always
        carries two ``after_process`` hooks: the failure recorder (see
        :func:`_record_saq_failure`) and the restart-sentinel check (see
        :func:`_check_restart_sentinel`) — an after-job placement is what
        lets a restart stop the worker without cancelling the boundary job.
        Caller kwargs pass through untouched; a caller-supplied
        ``before_process`` is passed through unchanged, and a
        caller-supplied ``after_process`` is merged to run before the
        built-ins.
        """
        from saq import Worker

        functions = [(entry.name, _adapt_sa_handler(entry.fn)) for entry in registry.values()]
        after_hooks = [_record_saq_failure, _check_restart_sentinel]
        caller_after = kwargs.pop("after_process", None)
        if caller_after is not None:
            # saq accepts a single callable or a collection — normalize to
            # the collection form and prepend.
            after_hooks[:0] = (
                list(caller_after) if isinstance(caller_after, Collection) else [caller_after]
            )
        return Worker(
            self.queue,
            functions=functions,
            after_process=after_hooks,
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

    Strict on purpose: swallowing a failed clear would let a worker report
    a clean restart while the sentinel stays latched (the replacement then
    exits again — an outage masked as success). Callers that must not
    propagate (the after_process hook, which lets the in-flight job run
    regardless) catch and log; the CLI exit path consumes a sentinel that
    survived a clean exit, and only a failed clear there turns non-zero.
    """
    from fastplace.cache import cache

    await cache().forget(RESTART_SENTINEL_KEY)


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
