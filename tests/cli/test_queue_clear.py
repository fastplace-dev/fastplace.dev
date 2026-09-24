"""Task 17 CLI — `queue:clear`.

Clears PENDING queue jobs (not the failed-job ledger — that is queue:flush).
The count semantics come from the driver: MemoryQueue drains its deque and
reports how many it dropped. Being in the spec's destructive set, the command
carries the production confirmation guard (the queue:flush convention:
confirm unless --force when APP_ENV=production, unset defaults to production).
"""

from __future__ import annotations

import asyncio
import re

import pytest

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from _isolation import isolate_project_state  # noqa: F401
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Rich colorizes output when the environment forces color; strip codes so
# assertions match on plain text.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fresh_queue_state():
    """Registry, queue singleton, and store singleton never leak between tests."""
    from fastplace.queue import reset_queue, reset_registry
    from fastplace.queue_failures import reset_failed_job_store

    reset_registry()
    reset_queue()
    reset_failed_job_store()
    yield
    reset_registry()
    reset_queue()
    reset_failed_job_store()


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A tmp project on the memory driver — clear is testable in-process."""
    monkeypatch.setenv("QUEUE_DRIVER", "memory")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _seed(count: int) -> None:
    """Register a no-op handler and queue ``count`` dispatches on the singleton."""
    from fastplace.queue import Job, queue

    @Job()
    async def clear_probe() -> None:
        return None

    async def _dispatch_all() -> None:
        for _ in range(count):
            await queue().dispatch("clear_probe")

    asyncio.run(_dispatch_all())


# ---------------------------------------------------------------------------
# the happy path — pending jobs dropped, count reported
# ---------------------------------------------------------------------------


def test_queue_clear_drains_memory_queue(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    _seed(2)

    result = runner.invoke(cli_app, ["queue:clear"])
    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "cleared" in plain
    assert "2" in plain

    from fastplace.queue import MemoryQueue, queue

    q = queue()
    assert isinstance(q, MemoryQueue)
    assert len(q.pending) == 0  # drained, nothing executed


def test_queue_clear_empty_queue_reports_no_pending(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")

    result = runner.invoke(cli_app, ["queue:clear"])
    assert result.exit_code == 0, result.output
    assert "no pending jobs" in ANSI_RE.sub("", result.output)


# ---------------------------------------------------------------------------
# the driver protocol — clear() is part of the surface every driver offers
# ---------------------------------------------------------------------------


def test_queue_driver_protocol_includes_clear(monkeypatch, tmp_path):
    """A fake driver with dispatch + clear satisfies the protocol, and the
    command drives whatever driver is installed through that clear()."""
    from fastplace.queue import MemoryQueue, QueueDriver, SaqQueue, set_queue

    # Both shipped drivers implement the new protocol method.
    assert hasattr(MemoryQueue(), "clear")
    assert hasattr(SaqQueue(), "clear")

    class FakeDriver:
        calls: list[str] = []

        async def dispatch(self, name: str, **kwargs: object) -> None: ...

        async def clear(self) -> int:
            self.calls.append("clear")
            return 3

    fake = FakeDriver()
    driver: QueueDriver = fake  # structural conformance: dispatch + clear
    set_queue(driver)
    monkeypatch.chdir(tmp_path)  # no project .env interferes with APP_ENV
    monkeypatch.setenv("APP_ENV", "testing")
    result = runner.invoke(cli_app, ["queue:clear"])

    assert result.exit_code == 0, result.output
    assert fake.calls == ["clear"]  # the command went through the driver
    assert "3" in ANSI_RE.sub("", result.output)


# ---------------------------------------------------------------------------
# the production guard — both confirm paths, plus --force
# ---------------------------------------------------------------------------


def test_queue_clear_production_confirm_decline_refuses(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    _seed(1)

    result = runner.invoke(cli_app, ["queue:clear"], input="n\n")
    assert result.exit_code == 1
    assert "aborted" in ANSI_RE.sub("", result.output)

    from fastplace.queue import MemoryQueue, queue

    q = queue()
    assert isinstance(q, MemoryQueue)
    assert len(q.pending) == 1  # untouched


def test_queue_clear_production_confirm_accept_proceeds(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    _seed(2)

    result = runner.invoke(cli_app, ["queue:clear"], input="y\n")
    assert result.exit_code == 0, result.output

    from fastplace.queue import MemoryQueue, queue

    q = queue()
    assert isinstance(q, MemoryQueue)
    assert len(q.pending) == 0  # confirmed, so cleared


def test_queue_clear_production_force_skips_the_prompt(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    _seed(1)

    result = runner.invoke(cli_app, ["queue:clear", "--force"])
    assert result.exit_code == 0, result.output
    assert "Delete every pending job" not in result.output  # no prompt

    from fastplace.queue import MemoryQueue, queue

    q = queue()
    assert isinstance(q, MemoryQueue)
    assert len(q.pending) == 0
