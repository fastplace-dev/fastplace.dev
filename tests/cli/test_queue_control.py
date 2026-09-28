"""Task 28 CLI — `queue:restart` + `queue:monitor`, and queue:work's restart
notice.

queue:restart only publishes the timestamped sentinel on the cache store;
workers honor it at their next job boundary and consume it as they exit (the
pinned roundtrip: set → worker exits → sentinel gone). queue:monitor prints
one depth row per named queue and exits 1 when any queue exceeds --max,
dispatching a "queue.busy" domain event per breach — listeners may not exist
in a CLI context, so a failing dispatch degrades to a warning, never an
error. Neither command is in the spec's destructive set: no production guard.
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
    """Registry, queue, store, listeners, and cache never leak between tests."""
    from fastplace.cache import reset_cache
    from fastplace.events import reset_listeners
    from fastplace.queue import reset_queue, reset_registry
    from fastplace.queue_failures import reset_failed_job_store

    reset_registry()
    reset_queue()
    reset_failed_job_store()
    reset_listeners()
    reset_cache()
    yield
    reset_registry()
    reset_queue()
    reset_failed_job_store()
    reset_listeners()
    reset_cache()


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A tmp project on the memory driver — both commands run in-process."""
    monkeypatch.setenv("QUEUE_DRIVER", "memory")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _seed(name: str = "t28_probe", count: int = 1) -> list[str]:
    """Register a recording handler and queue ``count`` dispatches; returns
    the list the handler appends to."""
    from fastplace.queue import Job, queue

    ran: list[str] = []

    @Job(name=name)
    async def probe() -> None:
        ran.append(name)

    async def _dispatch_all() -> None:
        for _ in range(count):
            await queue().dispatch(name)

    asyncio.run(_dispatch_all())
    return ran


# ---------------------------------------------------------------------------
# queue:restart — publish the sentinel
# ---------------------------------------------------------------------------


def test_queue_restart_publishes_the_sentinel(project):
    result = runner.invoke(cli_app, ["queue:restart"])
    assert result.exit_code == 0, result.output
    assert "restart" in ANSI_RE.sub("", result.output).lower()

    from fastplace.queue import restart_requested_at

    requested = asyncio.run(restart_requested_at())
    assert requested is not None, "the command must actually publish the sentinel"


# ---------------------------------------------------------------------------
# queue:work — the restart roundtrip: honor, notice, consume, exit 0
# ---------------------------------------------------------------------------


def test_queue_work_honors_a_pre_set_sentinel(project):
    """The pinned roundtrip: sentinel set → the worker exits 0 with a notice,
    the queued job is left for the replacement worker, and the sentinel is
    consumed so the replacement does not exit again immediately."""
    from fastplace.queue import set_restart_sentinel

    ran = _seed(count=1)
    asyncio.run(set_restart_sentinel())

    result = runner.invoke(cli_app, ["queue:work"])
    assert result.exit_code == 0, result.output

    plain = ANSI_RE.sub("", result.output)
    assert "restart" in plain.lower(), "the exit notice must say why"

    from fastplace.queue import MemoryQueue, queue, restart_requested_at

    q = queue()
    assert isinstance(q, MemoryQueue)
    assert ran == []  # the job was NOT executed
    assert len(q.pending) == 1  # it stays queued for the replacement worker
    assert "no pending jobs" not in plain  # there IS a pending job — no lie
    assert asyncio.run(restart_requested_at()) is None  # consumed on exit


def test_queue_work_without_sentinel_drains_normally(project):
    """No sentinel → the ordinary drain, no restart notice."""
    ran = _seed(count=2)

    result = runner.invoke(cli_app, ["queue:work"])
    assert result.exit_code == 0, result.output

    plain = ANSI_RE.sub("", result.output)
    assert ran == ["t28_probe", "t28_probe"]
    assert "restart" not in plain.lower()
    assert "processed 2 job(s)" in plain


def test_queue_work_saq_prints_notice_when_sentinel_pending(project, monkeypatch):
    """The saq branch with a pending sentinel: the worker's after_process
    hook consumes the sentinel at the job boundary as it exits — the CLI's
    exit verification finds it gone and reports a clean restart (exit 0)."""
    from fastplace.queue import SaqQueue, clear_restart_sentinel, set_restart_sentinel

    class FakeWorker:
        async def start(self) -> None:
            # Simulate one job boundary: the installed after_process hook
            # consumed the sentinel before the worker's event loop ended.
            await clear_restart_sentinel()

        async def stop(self) -> None:
            return None

    def fake_build_worker(self, **kwargs):  # noqa: ANN001
        return FakeWorker()

    monkeypatch.setattr(SaqQueue, "build_worker", fake_build_worker)
    monkeypatch.setenv("QUEUE_DRIVER", "saq")
    asyncio.run(set_restart_sentinel())

    result = runner.invoke(cli_app, ["queue:work"])
    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "restart requested" in plain.lower()
    assert "exited cleanly" in plain.lower()


def test_queue_work_saq_exit_fails_when_the_sentinel_survives(project, monkeypatch):
    """The restart contract's verification: a worker that exits WITHOUT
    consuming a pending sentinel never completed the restart — exit 1 with
    the failure line, never a silent clean exit."""
    from fastplace.queue import SaqQueue, set_restart_sentinel

    class FakeWorker:
        async def start(self) -> None:
            return None  # exits without reaching any job boundary

    def fake_build_worker(self, **kwargs):  # noqa: ANN001
        return FakeWorker()

    monkeypatch.setattr(SaqQueue, "build_worker", fake_build_worker)
    monkeypatch.setenv("QUEUE_DRIVER", "saq")
    asyncio.run(set_restart_sentinel())

    result = runner.invoke(cli_app, ["queue:work"])
    assert result.exit_code == 1, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "still set" in plain


# ---------------------------------------------------------------------------
# queue:monitor — depths, thresholds, and the queue.busy event
# ---------------------------------------------------------------------------


def test_queue_monitor_under_threshold_exits_0(project):
    _seed(count=2)

    result = runner.invoke(cli_app, ["queue:monitor", "default", "--max", "5"])
    assert result.exit_code == 0, result.output

    plain = ANSI_RE.sub("", result.output)
    assert "default" in plain
    assert "2" in plain, "the depth must be printed"


def test_queue_monitor_over_threshold_exits_1_and_dispatches(project):
    _seed(count=3)

    from fastplace.events import listen

    events: list = []
    listen("queue.busy", events.append)

    result = runner.invoke(cli_app, ["queue:monitor", "default", "--max", "2"])
    assert result.exit_code == 1

    plain = ANSI_RE.sub("", result.output)
    assert "default" in plain and "3" in plain
    assert len(events) == 1, "one queue.busy event per breaching queue"
    assert events[0].name == "queue.busy"
    assert events[0].payload == {"queue": "default", "depth": 3, "max": 2}


def test_queue_monitor_no_event_under_threshold(project):
    from fastplace.events import listen

    _seed(count=1)
    events: list = []
    listen("queue.busy", events.append)

    result = runner.invoke(cli_app, ["queue:monitor", "default", "--max", "9"])
    assert result.exit_code == 0, result.output
    assert events == []


def test_queue_monitor_multiple_queues_each_get_a_row(project):
    """QUEUES is a list — every name is resolved and printed (the memory
    driver has one in-process queue, so each name reports its depth)."""
    _seed(count=2)

    result = runner.invoke(cli_app, ["queue:monitor", "emails", "billing", "--max", "5"])
    assert result.exit_code == 0, result.output

    plain = ANSI_RE.sub("", result.output)
    assert "emails" in plain and "billing" in plain


def test_queue_monitor_survives_a_raising_listener(project):
    """The brief's graceful-degradation clause: a listener that raises (or no
    listener machinery at all) must not mask the depth verdict — the breach
    still exits 1 with a warning instead of a traceback."""
    from fastplace.events import listen

    def explosive(event):  # noqa: ANN001 — listener signature is (DomainEvent)
        raise RuntimeError("listener machinery down")

    listen("queue.busy", explosive)
    _seed(count=3)

    result = runner.invoke(cli_app, ["queue:monitor", "default", "--max", "1"])
    assert result.exit_code == 1
    # CliRunner records a handled exit as SystemExit; the listener's
    # RuntimeError would surface as itself — it must have been swallowed
    # to a warning instead of crashing the command.
    assert isinstance(result.exception, SystemExit), "a failing listener is a warning, not a crash"
    assert not isinstance(result.exception, RuntimeError)
    plain = ANSI_RE.sub("", result.output)
    assert "queue.busy" in plain, "the dispatch failure must be visible"


def test_queue_monitor_help_documents_the_threshold(project):
    result = runner.invoke(cli_app, ["queue:monitor", "--help"])
    assert result.exit_code == 0, result.output
    assert "--max" in result.output


def test_queue_monitor_saq_rows_show_active_depth(project, monkeypatch):
    """q2-G5: on the saq driver each row also reports ACTIVE jobs — a worker
    that died mid-job leaves jobs stuck ACTIVE (the sweeper aborts them
    ~90s later), so depth alone hides the crash symptom."""
    from fastplace.queue import SaqQueue

    monkeypatch.setenv("QUEUE_DRIVER", "saq")

    async def _fake_depth(self) -> int:  # noqa: ANN001 — test seam
        return 4

    async def _fake_active(self) -> int:  # noqa: ANN001 — test seam
        return 2

    monkeypatch.setattr(SaqQueue, "queue_depth", _fake_depth)
    monkeypatch.setattr(SaqQueue, "active_depth", _fake_active)

    result = runner.invoke(cli_app, ["queue:monitor", "default", "--max", "5"])
    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "active" in plain  # the column exists…
    assert "2" in plain  # …and carries the stuck-job count, not just depth


def test_queue_work_failure_line_renders_markup_literally(project):
    """Job names and handler errors are data, not Rich markup — a `[bold]`
    name / `[red]` error must print literally (final review), never styled."""
    from fastplace.queue import Job, queue

    @Job(name="t31-[bold]boom[/]")
    async def explode() -> None:
        raise RuntimeError("detonated [red]now[/]")

    async def _dispatch() -> None:
        await queue().dispatch("t31-[bold]boom[/]")

    asyncio.run(_dispatch())

    result = runner.invoke(cli_app, ["queue:work"])
    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "t31-[bold]boom[/] failed: detonated [red]now[/]" in plain
