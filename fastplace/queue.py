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

import importlib
import inspect
import pkgutil
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from fastplace.config import config
from fastplace.errors import ConfigurationError

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


class MemoryQueue:
    """In-process driver: dispatch validates + queues, run_pending drains."""

    def __init__(self) -> None:
        self.pending: deque[_Pending] = deque()
        self.failures: list[_Failure] = []

    async def dispatch(self, name: str, **kwargs: Any) -> None:
        if name not in registry:
            raise ValueError(f"unknown job '{name}' — is it registered with @Job?")
        self.pending.append(_Pending(name=name, kwargs=kwargs))

    async def run_pending(self) -> int:
        """Execute the queued jobs; a failing handler is recorded, not raised.

        Only the batch present at entry is drained: a job whose side effect
        enqueues more work (a domain event → another job) chains to the *next*
        drain instead of looping this one forever. The failure ledger is
        scoped to the batch — a long-lived process draining repeatedly must
        not accumulate exception objects forever.
        """
        self.failures = []
        batch = list(self.pending)
        self.pending.clear()
        executed = 0
        for item in batch:
            try:
                await registry[item.name].fn(**item.kwargs)
            except Exception as exc:  # noqa: BLE001 — isolation is the contract
                self.failures.append(_Failure(name=item.name, error=exc))
            executed += 1
        return executed


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

    def build_worker(self, **kwargs: Any) -> Any:
        """Assemble a saq Worker over the registry (no network until start)."""
        from saq import Worker

        functions = [(entry.name, entry.fn) for entry in registry.values()]
        return Worker(self.queue, functions=functions, **kwargs)


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
