"""``queue:work`` runtime options — translation to installed-saq worker kwargs.

Spec E1: ``--tries --timeout --sleep --max-jobs --queue --stop-when-empty``.
Options the INSTALLED saq ``Worker.__init__`` accepts become worker kwargs
(``max_burst_jobs``, ``burst``); the rest are documented no-ops printed as a
warning line instead of silently vanishing or crashing the worker build.
"""

from __future__ import annotations

import asyncio
import inspect
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
def _fresh_queue():
    """The process-wide queue singleton must not leak between tests."""
    from fastplace.queue import reset_queue

    reset_queue()
    yield
    reset_queue()


class _FakeWorker:
    """Stands in for saq.Worker — records start(), touches no network."""

    def __init__(self) -> None:
        self.started = False

    async def start(self) -> None:
        self.started = True


@pytest.fixture()
def capture_build_worker(monkeypatch):
    """Monkeypatch ``SaqQueue.build_worker`` to capture kwargs; returns the
    dict the CLI handed through, plus the last fake worker built."""
    from fastplace.queue import SaqQueue

    captured: dict = {"kwargs": {}, "worker": None}

    def fake_build_worker(self, **kwargs):
        captured["kwargs"] = dict(kwargs)
        captured["worker"] = _FakeWorker()
        return captured["worker"]

    monkeypatch.setattr(SaqQueue, "build_worker", fake_build_worker)
    return captured


# ---------------------------------------------------------------------------
# memory driver — options are accepted, documented as no-ops, drain intact
# ---------------------------------------------------------------------------


def test_memory_driver_drains_with_options_and_warns_no_ops(tmp_path, monkeypatch):
    """The brief's canonical invocation: exit 0, the drain runs, and every
    worker-loop option is reported as a no-op (memory has no worker loop)."""
    from fastplace.queue import MemoryQueue

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QUEUE_DRIVER", raising=False)

    drains = []

    async def capture_drain(self):
        drains.append(len(self.pending))
        return 0

    monkeypatch.setattr(MemoryQueue, "run_pending", capture_drain)

    result = runner.invoke(cli_app, ["queue:work", "--once", "--tries=3", "--max-jobs=2"])
    assert result.exit_code == 0, result.output
    assert drains == [0], "the memory drain (its worker) must still run once"

    plain = ANSI_RE.sub("", result.output)
    assert "--tries=3" in plain, "no-op options must be documented on the line"
    assert "--max-jobs=2" in plain


def test_memory_driver_options_do_not_break_a_real_drain(tmp_path, monkeypatch):
    """A dispatched job still executes when runtime options are present."""
    from fastplace.queue import Job, queue

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QUEUE_DRIVER", raising=False)

    ran: list[int] = []

    @Job(name="t14_memory_options_job")
    async def job(n: int) -> None:
        ran.append(n)

    q = queue()  # the same singleton the CLI command drains
    asyncio.run(q.dispatch("t14_memory_options_job", n=7))

    result = runner.invoke(cli_app, ["queue:work", "--once", "--sleep=1", "--timeout=9"])
    assert result.exit_code == 0, result.output
    assert ran == [7]


def test_memory_driver_warns_queue_option_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QUEUE_DRIVER", raising=False)

    result = runner.invoke(cli_app, ["queue:work", "--queue", "emails"])
    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "--queue" in plain and "emails" in plain


# ---------------------------------------------------------------------------
# saq driver — supported options become worker kwargs
# ---------------------------------------------------------------------------


def test_saq_receives_supported_options_and_warns_unsupported(
    tmp_path, monkeypatch, capture_build_worker
):
    """The brief's canonical invocation against the saq wiring: --max-jobs
    maps to max_burst_jobs and --once to burst=True (with the positive
    dequeue_timeout installed saq requires in burst mode); --tries is not a
    Worker parameter in installed saq, so it must be warned, not passed."""
    import saq
    from saq import Worker

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QUEUE_DRIVER", "saq")

    result = runner.invoke(cli_app, ["queue:work", "--once", "--tries=3", "--max-jobs=2"])
    assert result.exit_code == 0, result.output

    kwargs = capture_build_worker["kwargs"]
    # Honest to the INSTALLED saq (verified against Worker.__init__): the
    # premise of this task's supported/no-op split.
    worker_params = inspect.signature(Worker.__init__).parameters
    assert "max_burst_jobs" in worker_params
    assert "tries" not in worker_params
    assert kwargs == {"burst": True, "max_burst_jobs": 2, "dequeue_timeout": 1.0}
    assert capture_build_worker["worker"].started

    plain = ANSI_RE.sub("", result.output)
    assert f"saq {saq.__version__}" in plain, "warning names the installed version"
    assert "--tries=3" in plain


def test_saq_stop_when_empty_maps_to_burst(tmp_path, monkeypatch, capture_build_worker):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QUEUE_DRIVER", "saq")

    result = runner.invoke(cli_app, ["queue:work", "--stop-when-empty"])
    assert result.exit_code == 0, result.output
    assert capture_build_worker["kwargs"] == {"burst": True, "dequeue_timeout": 1.0}


def test_saq_sleep_and_timeout_are_warned_no_ops(tmp_path, monkeypatch, capture_build_worker):
    """Neither ``sleep`` nor ``timeout`` exists on the installed Worker —
    poll_interval is a Postgres dequeue-strategy toggle, not an idle sleep,
    so mapping it would lie. Both must be warned and never passed."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QUEUE_DRIVER", "saq")

    result = runner.invoke(cli_app, ["queue:work", "--sleep=2", "--timeout=60"])
    assert result.exit_code == 0, result.output

    kwargs = capture_build_worker["kwargs"]
    assert "sleep" not in kwargs and "poll_interval" not in kwargs
    assert "timeout" not in kwargs

    plain = ANSI_RE.sub("", result.output)
    assert "--sleep=2" in plain
    assert "--timeout=60" in plain


def test_saq_queue_option_selects_the_queue_name(tmp_path, monkeypatch, capture_build_worker):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QUEUE_DRIVER", "saq")

    result = runner.invoke(cli_app, ["queue:work", "--queue", "emails"])
    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "emails" in plain, "the worked queue name must be visible"


def test_queue_work_help_lists_every_runtime_option():
    result = runner.invoke(cli_app, ["queue:work", "--help"])
    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    for flag in (
        "--tries",
        "--timeout",
        "--sleep",
        "--max-jobs",
        "--queue",
        "--stop-when-empty",
    ):
        assert flag in plain, flag
