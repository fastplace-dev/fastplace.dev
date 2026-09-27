"""T4.7 — queue: @Job registry, memory driver, saq driver (construct-only).

Redis is never contacted: the saq driver is built lazily and tested either
construct-only or with an injected fake queue — redis-py hangs without a
server, so no test may apply/enqueue against a real client.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import textwrap
import time

import pytest
from typer.testing import CliRunner

from fastplace.queue import (
    Job,
    MemoryQueue,
    SaqQueue,
    import_jobs,
    jobs,
    queue,
    registered_jobs,
    reset_queue,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _fresh_queue():
    reset_registry()
    reset_queue()
    yield
    reset_registry()
    reset_queue()


# ---------------------------------------------------------------------------
# @Job decorator + registry
# ---------------------------------------------------------------------------


async def test_job_decorator_registers_async_function():
    @Job()
    async def send_invoice(job_id: int):
        return job_id

    entry = jobs()["send_invoice"]
    assert entry.name == "send_invoice"
    assert entry.fn is send_invoice  # decorator passes the fn through
    assert await send_invoice(3) == 3


def test_job_decorator_rejects_sync_function():
    def not_async():
        return 1

    with pytest.raises(ValueError, match="async"):
        Job()(not_async)


def test_job_name_can_be_overridden():
    @Job(name="billing.reconcile")
    async def reconcile():
        return None

    assert "billing.reconcile" in registered_jobs()
    assert jobs()["billing.reconcile"].fn is reconcile


def test_duplicate_job_names_are_rejected_at_registration():
    @Job()
    async def duplicate():
        return None

    with pytest.raises(ValueError, match="duplicate"):

        @Job()  # noqa: F811 — same name on purpose
        async def duplicate():
            return None


def test_reimport_after_module_eviction_replaces_stale_registration(tmp_path, monkeypatch):
    root = _make_project(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/jobs/__init__.py": "",
            "app/jobs/mail.py": """
                from fastplace.queue import Job

                @Job(name="mail_send")
                async def mail_send(message: dict):
                    return message
            """,
        },
    )
    monkeypatch.syspath_prepend(str(root))

    # Earlier tests in the suite may leave a foreign root's `app` package
    # cached — sweep so this import resolves against THIS project's root.
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)

    importlib.import_module("app.jobs.mail")  # first import registers
    first = jobs()["mail_send"].fn

    # Evict the module (what boot sandboxes do between/within boots), then
    # re-import: a NEW function object re-registers the same dotted path.
    sys.modules.pop("app.jobs.mail", None)
    sys.modules.pop("app.jobs", None)
    sys.modules.pop("app", None)
    importlib.import_module("app.jobs.mail")

    entry = jobs()["mail_send"]
    assert entry.fn is not first
    assert entry.fn.__module__ == "app.jobs.mail"


# ---------------------------------------------------------------------------
# import_jobs discovery
# ---------------------------------------------------------------------------


def _make_project(tmp_path, files: dict[str, str]) -> object:
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content))
    return tmp_path


class _ProjectImports:
    """Import a tmp project's app.jobs package with sys.path scoped."""

    def __init__(self, root):
        self.root = root

    def __enter__(self):
        sys.path.insert(0, str(self.root))
        return self

    def __exit__(self, *exc):
        sys.modules.pop("app", None)
        sys.modules.pop("app.jobs", None)
        sys.path.remove(str(self.root))


def test_import_jobs_discovers_app_jobs(tmp_path):
    root = _make_project(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/jobs/__init__.py": "",
            "app/jobs/email_job.py": """
                from fastplace.queue import Job

                @Job()
                async def welcome_email(user_id: int):
                    return user_id
            """,
        },
    )
    with _ProjectImports(root):
        found = import_jobs(root)
    assert found == ["welcome_email"]


def test_import_jobs_skips_private_modules(tmp_path):
    root = _make_project(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/jobs/__init__.py": "",
            "app/jobs/_base.py": """
                from fastplace.queue import Job

                @Job()
                async def hidden():
                    return None
            """,
        },
    )
    with _ProjectImports(root):
        found = import_jobs(root)
    assert found == []


def test_import_jobs_missing_directory_returns_empty(tmp_path):
    root = tmp_path / "nowhere"
    root.mkdir()
    assert import_jobs(root) == []


def test_import_jobs_uses_the_given_root_over_stale_modules(tmp_path, monkeypatch):
    # A cached `app` package from another root must not shadow the project
    # the caller explicitly passed.
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    for proj, job in ((proj_a, "alpha"), (proj_b, "beta")):
        (proj / "app" / "jobs").mkdir(parents=True)
        (proj / "app" / "__init__.py").write_text("")
        (proj / "app" / "jobs" / "__init__.py").write_text("")
        (proj / "app" / "jobs" / f"{job}_job.py").write_text(
            f"from fastplace.queue import Job\n\n\n@Job()\nasync def {job}():\n    return None\n"
        )

    monkeypatch.chdir(proj_a)
    sys.path.insert(0, str(proj_a))
    try:
        import_jobs(proj_a)  # leaves `app` bound to proj_a in sys.modules
    finally:
        sys.path.remove(str(proj_a))

    found = import_jobs(proj_b)
    assert "beta" in found  # b's jobs imported despite the stale `app` module

    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)


def test_import_jobs_handles_sibling_prefixed_roots(tmp_path):
    # Substring containment ("/…/a" in "/…/a-v2/…") lets a sibling project's
    # cached `app` survive the stale sweep and silently shadow this root's
    # jobs — real path containment must evict it.
    proj_a = tmp_path / "a"
    proj_a_v2 = tmp_path / "a-v2"
    for proj, _job in ((proj_a, "alpha"), (proj_a_v2, None)):
        (proj / "app" / "jobs").mkdir(parents=True)
        (proj / "app" / "__init__.py").write_text("")
        (proj / "app" / "jobs" / "__init__.py").write_text("")
    (proj_a / "app" / "jobs" / "alpha_job.py").write_text(
        "from fastplace.queue import Job\n\n\n@Job()\nasync def alpha():\n    return None\n"
    )

    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }
    for name in saved:
        sys.modules.pop(name)
    sys.path.insert(0, str(proj_a_v2))
    try:
        import app as sibling_app  # noqa: F401 — cached on purpose
    finally:
        sys.path.remove(str(proj_a_v2))

    try:
        found = import_jobs(proj_a)
        assert found == ["alpha"]  # a's own jobs discovered despite the sibling
        assert sys.modules.get("app") is not sibling_app
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


# ---------------------------------------------------------------------------
# MemoryQueue driver
# ---------------------------------------------------------------------------


async def test_memory_dispatch_validates_and_queues():
    @Job()
    async def ping(value: str):
        return f"pong:{value}"

    mem = MemoryQueue()
    await mem.dispatch("ping", value="x")
    assert len(mem.pending) == 1
    assert mem.pending[0].name == "ping"

    with pytest.raises(ValueError, match="unknown job"):
        await mem.dispatch("does_not_exist")


async def test_memory_run_pending_executes_with_kwargs():
    results = []

    @Job()
    async def record(item: int):
        results.append(item)

    mem = MemoryQueue()
    await mem.dispatch("record", item=1)
    await mem.dispatch("record", item=2)
    executed = await mem.run_pending()
    assert executed == 2
    assert results == [1, 2]
    assert len(mem.pending) == 0  # drained


async def test_memory_failure_is_isolated_and_captured():
    results = []

    @Job()
    async def boom():
        raise RuntimeError("kaput")

    @Job()
    async def fine(item: str):
        results.append(item)

    mem = MemoryQueue()
    await mem.dispatch("boom")
    await mem.dispatch("fine", item="survived")
    executed = await mem.run_pending()

    assert executed == 2  # both were *run*; the failure didn't stop the queue
    assert results == ["survived"]
    assert len(mem.failures) == 1
    assert mem.failures[0].name == "boom"
    assert isinstance(mem.failures[0].error, RuntimeError)


async def test_memory_run_pending_with_nothing_queued():
    executed = await MemoryQueue().run_pending()
    assert executed == 0


async def test_memory_failures_are_scoped_to_the_drain_batch():
    # Long-lived processes drain repeatedly — a failure from an old batch
    # must not accumulate forever.
    @Job()
    async def boom():
        raise RuntimeError("kaput")

    @Job()
    async def fine():
        return None

    mem = MemoryQueue()
    await mem.dispatch("boom")
    await mem.run_pending()
    assert len(mem.failures) == 1

    await mem.dispatch("fine")
    await mem.run_pending()
    assert mem.failures == []  # the clean second drain reset the ledger


# ---------------------------------------------------------------------------
# SaqQueue driver — construct-only / injected fake, never a live server
# ---------------------------------------------------------------------------


def test_saq_construct_only_from_url():
    # Building the driver must not connect to redis (lazy queue).
    driver = SaqQueue(url="redis://localhost:6379/2", name="fastplace")
    assert driver._queue is None


def test_saq_queue_builds_redis_queue_lazily():
    driver = SaqQueue(url="redis://localhost:6379/2", name="fastplace")
    saq_queue = driver.queue
    assert type(saq_queue).__name__ == "RedisQueue"
    assert saq_queue.name == "fastplace"


async def test_saq_dispatch_wraps_kwargs_to_avoid_field_hijack():
    # saq's enqueue splats kwargs and hijacks any name matching its Job
    # dataclass fields (timeout, ttl, kwargs…) into job properties — the
    # payload must arrive as one explicit kwargs dict instead.
    class FakeSaqQueue:
        def __init__(self):
            self.enqueued = []

        async def enqueue(self, name, **kwargs):
            self.enqueued.append((name, kwargs))
            return None

    fake = FakeSaqQueue()
    driver = SaqQueue(queue=fake)

    @Job()
    async def notify(user_id: int, timeout: str = ""):
        return None

    await driver.dispatch("notify", user_id=9, timeout="safe")
    assert fake.enqueued == [("notify", {"kwargs": {"user_id": 9, "timeout": "safe"}})]


def test_saq_worker_assembly_registers_jobs_by_name():
    @Job(name="billing.reconcile")
    async def reconcile():
        return None

    driver = SaqQueue(url="redis://localhost:6379/2", name="fastplace")
    worker = driver.build_worker()
    assert "billing.reconcile" in worker.functions


# ---------------------------------------------------------------------------
# build_worker handler shape — saq calls fn(ctx, **kwargs); @Job handlers are
# documented as fn(**kwargs). The worker must adapt, not the developer.
# ---------------------------------------------------------------------------


async def test_saq_worker_function_accepts_ctx_and_forwards_kwargs():
    """saq's process() invokes ``function(context, **kwargs)`` — the registered
    callable must accept that leading ctx positional and hand the handler
    ONLY its dispatched kwargs (q1-G1/q2-G1)."""
    seen: dict = {}

    @Job(name="shape.kwargs_probe")
    async def probe(user_id: int, timeout: str = "unset"):
        seen["call"] = (user_id, timeout)
        return "done"

    driver = SaqQueue(url="redis://localhost:6379/2", name="fastplace")
    worker = driver.build_worker()

    result = await worker.functions["shape.kwargs_probe"](
        {"worker": object(), "job": object()},  # saq's ctx positional
        user_id=3,
        timeout="payload",  # a Job-dataclass field name — must stay a kwarg
    )
    assert seen["call"] == (3, "payload")
    assert result == "done"


async def test_saq_worker_function_shields_no_kwarg_handler_from_ctx():
    """A no-kwargs handler dispatched with no kwargs must receive nothing —
    the bare-handler bug silently fed it saq's ctx dict as its first
    declared parameter."""
    ran: list[object] = []

    @Job(name="shape.bare_probe")
    async def bare():
        ran.append(True)
        return None

    driver = SaqQueue(url="redis://localhost:6379/2", name="fastplace")
    worker = driver.build_worker()

    result = await worker.functions["shape.bare_probe"]({"worker": object()})
    assert result is None
    assert ran == [True]


# ---------------------------------------------------------------------------
# SaqQueue.dispatch_delayed — scheduled enqueue via an explicit Job
# ---------------------------------------------------------------------------


class _RecordingSaqQueue:
    """Fake saq queue: enqueue records the Job object — no redis, no network."""

    def __init__(self):
        self.recorded = []

    async def enqueue(self, job):
        self.recorded.append(job)
        return job


@pytest.fixture
def fake_saq_queue():
    """SaqQueue over the recording fake, with a handler registered for dispatch.

    Registry isolation rides the autouse ``_fresh_queue`` fixture.
    """

    @Job()
    async def resize(path: str = "", size: int = 0):
        return None

    return SaqQueue(queue=_RecordingSaqQueue())


async def test_dispatch_delayed_builds_explicit_job_with_epoch_seconds_schedule(fake_saq_queue):
    recorded = fake_saq_queue.queue.recorded

    before = time.time()
    await fake_saq_queue.dispatch_delayed("resize", {"path": "a.png", "size": 64}, delay=5.0)
    after = time.time()

    (job,) = recorded
    assert job.function == "resize"
    assert job.kwargs == {"path": "a.png", "size": 64}  # intact — no field hijack
    # Installed saq (0.26.4) holds a job until its ``scheduled`` field — an
    # absolute epoch-seconds timestamp — comes due, so the value must be
    # now + delay. Adding an integer shifts the floor exactly, so the window
    # is closed-form with no sleep.
    assert int(before) + 5 <= job.scheduled <= int(after) + 5


async def test_dispatch_delayed_defaults(fake_saq_queue):
    recorded = fake_saq_queue.queue.recorded

    before = time.time()
    await fake_saq_queue.dispatch_delayed("resize")  # both defaults ride
    after = time.time()

    (job,) = recorded
    assert job.function == "resize"
    assert job.kwargs == {}  # None normalized to empty dict
    assert int(before) + 1 <= job.scheduled <= int(after) + 1  # delay=1.0 default


async def test_dispatch_delayed_unknown_name_raises(fake_saq_queue):
    with pytest.raises(ValueError, match="unknown job 'nope'"):
        await fake_saq_queue.dispatch_delayed("nope")
    assert fake_saq_queue.queue.recorded == []  # validation fired before any enqueue


# ---------------------------------------------------------------------------
# queue() factory
# ---------------------------------------------------------------------------


async def test_factory_defaults_to_memory(monkeypatch):
    monkeypatch.delenv("QUEUE_DRIVER", raising=False)
    assert isinstance(queue(), MemoryQueue)


async def test_factory_returns_singleton(monkeypatch):
    monkeypatch.delenv("QUEUE_DRIVER", raising=False)
    assert queue() is queue()


async def test_factory_selects_saq_driver(monkeypatch):
    monkeypatch.setenv("QUEUE_DRIVER", "saq")
    assert isinstance(queue(), SaqQueue)


async def test_factory_rejects_unknown_driver(monkeypatch):
    monkeypatch.setenv("QUEUE_DRIVER", "celery")
    with pytest.raises(Exception, match="celery"):
        queue()


# ---------------------------------------------------------------------------
# CLI — queue:work
# ---------------------------------------------------------------------------


def test_queue_work_once_drains_memory_queue(tmp_path, monkeypatch):
    from fastplace.cli import app as root_cli

    monkeypatch.chdir(tmp_path)
    reset_registry()
    reset_queue()

    ran = []

    @Job()
    async def cli_job(n: int):
        ran.append(n)

    q = queue()  # same singleton the CLI command uses
    asyncio.run(q.dispatch("cli_job", n=42))

    result = CliRunner().invoke(root_cli, ["queue:work", "--once"])
    assert result.exit_code == 0, result.output
    assert ran == [42]
    assert "1" in result.output


def test_queue_work_once_with_empty_queue(tmp_path, monkeypatch):
    from fastplace.cli import app as root_cli

    monkeypatch.chdir(tmp_path)
    reset_registry()
    result = CliRunner().invoke(root_cli, ["queue:work", "--once"])
    assert result.exit_code == 0, result.output
    assert "no pending" in result.output.lower()


# ---------------------------------------------------------------------------
# CLI queue:work — saq branch restart-exit honesty (q1-G2/q2-G3)
# ---------------------------------------------------------------------------


class _FakeSaqWorker:
    """start() returns immediately — the CLI's sentinel-verification exit
    path is what's under test, not saq itself."""

    def __init__(self, on_start=None) -> None:
        self.started = False
        self._on_start = on_start

    async def start(self) -> None:
        self.started = True
        if self._on_start is not None:
            await self._on_start()  # e.g. the hook consuming the sentinel


def _patch_saq_worker(monkeypatch, worker: _FakeSaqWorker) -> None:
    import fastplace.queue as queue_module

    monkeypatch.setenv("QUEUE_DRIVER", "saq")
    monkeypatch.setattr(
        queue_module.SaqQueue, "build_worker", lambda self, **kwargs: worker
    )


def test_queue_work_saq_exit_is_clean_when_sentinel_was_consumed(tmp_path, monkeypatch):
    """A restart honored mid-run must end exit 0 — the hook consumed the
    sentinel and the worker finished its in-flight job."""
    from fastplace import queue as queue_module
    from fastplace.cli import app as root_cli

    monkeypatch.chdir(tmp_path)
    reset_registry()
    reset_queue()

    asyncio.run(queue_module.set_restart_sentinel())
    # The worker honors the restart during its run: consumes the sentinel.
    worker = _FakeSaqWorker(on_start=queue_module.clear_restart_sentinel)

    _patch_saq_worker(monkeypatch, worker)
    result = CliRunner().invoke(root_cli, ["queue:work"])
    assert result.exit_code == 0, result.output


def test_queue_work_saq_exits_nonzero_when_sentinel_survives(tmp_path, monkeypatch):
    """A restart whose sentinel is STILL SET after the worker stopped means
    the restart contract broke (nothing consumed it) — success output here
    would mask a worker that never honors restarts. Exit 1, loudly."""
    from fastplace import queue as queue_module
    from fastplace.cli import app as root_cli

    monkeypatch.chdir(tmp_path)
    reset_registry()
    reset_queue()

    asyncio.run(queue_module.set_restart_sentinel())

    _patch_saq_worker(monkeypatch, _FakeSaqWorker())
    result = CliRunner().invoke(root_cli, ["queue:work"])
    assert result.exit_code == 1
    assert "sentinel" in result.output.lower()


# ---------------------------------------------------------------------------
# set_queue — installing a custom driver as the process default
# ---------------------------------------------------------------------------
async def test_set_queue_installs_a_custom_driver():
    """Wrappers (e.g. fastplace-tenancy's TenantQueue) must be installable
    as the process default so every dispatch — including the domain-event
    bridge — flows through them."""
    from fastplace.queue import set_queue

    custom = MemoryQueue()
    set_queue(custom)
    assert queue() is custom
    # reset_queue() (the autouse fixture here) clears it back to config.
