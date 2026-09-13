"""T4.7 — queue: @Job registry, memory driver, saq driver (construct-only).

Redis is never contacted: the saq driver is built lazily and tested either
construct-only or with an injected fake queue — redis-py hangs without a
server, so no test may apply/enqueue against a real client.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap

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
