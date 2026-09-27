"""q1-G3/q2-G4 — the dispatch reliability envelope.

Every dispatched job carries an explicit reliability envelope — retries,
timeout, backoff, ttl, delay, queue routing, uniqueness — resolved with one
precedence: builder options > ``@Job`` params > ``QUEUE_*`` env > framework
defaults. Both drivers honor the same envelope; the memory driver applies
retries/timeout/backoff inside ``run_pending`` so a dev drain behaves like a
production worker. ``dispatch`` returns a trackable handle (the saq Job on
redis, an in-process handle on memory) so application code can poll what it
dispatched (q1-G10).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from fastplace.queue import (
    Job,
    MemoryQueue,
    SaqQueue,
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


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Envelope env vars never leak between tests — each test states its own."""
    for key in ("QUEUE_TRIES", "QUEUE_TIMEOUT", "QUEUE_BACKOFF", "QUEUE_TTL"):
        monkeypatch.delenv(key, raising=False)


class _OptionsSaqQueue:
    """Fake saq queue: enqueue records the Job object; the return value is
    scriptable (saq returns None for a duplicate job key — the uniqueness
    mechanism the driver must pass through)."""

    def __init__(self) -> None:
        self.recorded: list[object] = []
        self.returns: list[object] | None = None

    async def enqueue(self, job):
        self.recorded.append(job)
        if self.returns is not None:
            return self.returns.pop(0)
        return job


@pytest.fixture
def saq_driver():
    @Job(name="env.probe")
    async def probe(user_id: int = 0, timeout: str = ""):
        return None

    return SaqQueue(queue=_OptionsSaqQueue())


# ---------------------------------------------------------------------------
# resolution — precedence: builder > @Job > env > framework defaults
# ---------------------------------------------------------------------------


async def test_saq_dispatch_resolves_framework_defaults(saq_driver):
    await saq_driver.dispatch("env.probe", user_id=1)
    job = saq_driver.queue.recorded[0]
    # The hidden saq dataclass defaults (timeout=10, retries=1, ttl=600) are
    # replaced by explicit framework defaults a team can reason about.
    assert job.retries == 3  # QUEUE_TRIES default — swept jobs stay retryable
    assert job.timeout == 60.0  # QUEUE_TIMEOUT default — not saq's hidden 10s
    assert job.ttl == 600
    assert job.retry_backoff is False  # off unless asked for
    assert job.retry_delay == 0
    assert job.scheduled == 0  # immediate


async def test_env_overrides_framework_defaults(saq_driver, monkeypatch):
    monkeypatch.setenv("QUEUE_TRIES", "5")
    monkeypatch.setenv("QUEUE_TIMEOUT", "9.5")
    monkeypatch.setenv("QUEUE_BACKOFF", "2")
    monkeypatch.setenv("QUEUE_TTL", "99")

    await saq_driver.dispatch("env.probe")
    job = saq_driver.queue.recorded[0]
    assert job.retries == 5
    assert job.timeout == 9.5
    assert job.ttl == 99
    # saq's retry_backoff is a MAX cap and retry_delay is the base — a base
    # of X seconds is expressed as retry_delay=X with an unbounded cap.
    assert job.retry_delay == 2.0
    assert job.retry_backoff is True


async def test_job_params_override_env(saq_driver, monkeypatch):
    monkeypatch.setenv("QUEUE_TRIES", "5")
    monkeypatch.setenv("QUEUE_TIMEOUT", "9.5")

    @Job(name="jobopts.probe", retries=7, timeout=12.5, backoff=3)
    async def decorated() -> None:
        return None

    await saq_driver.dispatch("jobopts.probe")
    job = saq_driver.queue.recorded[0]
    assert job.retries == 7
    assert job.timeout == 12.5
    assert job.retry_delay == 3.0
    assert job.retry_backoff is True


async def test_builder_options_override_job_params(saq_driver, monkeypatch):
    monkeypatch.setenv("QUEUE_TRIES", "5")

    @Job(name="build.probe", retries=7, timeout=30)
    async def decorated() -> None:
        return None

    await saq_driver.job("build.probe", retries=9, timeout=8.5, ttl=42).dispatch()
    job = saq_driver.queue.recorded[0]
    assert job.retries == 9  # builder beats @Job
    assert job.timeout == 8.5
    assert job.ttl == 42


async def test_zero_timeout_env_is_a_loud_configuration_error(saq_driver, monkeypatch):
    monkeypatch.setenv("QUEUE_TIMEOUT", "0")
    from fastplace.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="QUEUE_TIMEOUT"):
        await saq_driver.dispatch("env.probe")


# ---------------------------------------------------------------------------
# builder — delay, uniqueness, handles, queue routing (q1-G4/q1-G10)
# ---------------------------------------------------------------------------


async def test_builder_delay_schedules_an_absolute_epoch(saq_driver):
    before = time.time()
    await saq_driver.job("env.probe", delay=5).dispatch(user_id=2)
    after = time.time()
    job = saq_driver.queue.recorded[0]
    assert int(before) + 5 <= job.scheduled <= int(after) + 5


async def test_builder_unique_derives_a_stable_key(saq_driver):
    await saq_driver.job("env.probe", unique=True).dispatch(user_id=3)
    await saq_driver.job("env.probe", unique=True).dispatch(user_id=3)
    first, second = saq_driver.queue.recorded[:2]
    assert first.key == second.key  # same name + kwargs → same identity

    await saq_driver.job("env.probe", unique=True).dispatch(user_id=4)
    third = saq_driver.queue.recorded[2]
    assert third.key != first.key  # different payload → different identity

    await saq_driver.dispatch("env.probe", user_id=3)  # non-unique default
    fourth = saq_driver.queue.recorded[3]
    assert fourth.key != first.key


async def test_dispatch_returns_the_enqueued_job_handle(saq_driver):
    handle = await saq_driver.dispatch("env.probe")
    assert handle is saq_driver.queue.recorded[0]  # the saq Job itself

    saq_driver.queue.returns = [None]  # saq signals a duplicate key with None
    duplicate = await saq_driver.job("env.probe", unique=True).dispatch(user_id=3)
    assert duplicate is None  # suppressed duplicate — passed through honestly


async def test_builder_routes_to_a_named_queue(saq_driver):
    emails = _OptionsSaqQueue()
    saq_driver._routes["emails"] = emails  # the routing cache is the test seam

    await saq_driver.job("env.probe", queue="emails").dispatch(user_id=5)

    assert len(emails.recorded) == 1
    assert saq_driver.queue.recorded == []  # main queue untouched (q1-G4)


async def test_builder_unknown_name_raises_before_any_enqueue(saq_driver):
    with pytest.raises(ValueError, match="unknown job 'ghost'"):
        await saq_driver.job("ghost").dispatch()
    assert saq_driver.queue.recorded == []


async def test_invalid_builder_options_are_refused(saq_driver):
    with pytest.raises(ValueError, match="retries"):
        await saq_driver.job("env.probe", retries=0).dispatch()
    with pytest.raises(ValueError, match="timeout"):
        await saq_driver.job("env.probe", timeout=0).dispatch()
    with pytest.raises(ValueError, match="backoff"):
        await saq_driver.job("env.probe", backoff=-1).dispatch()
    with pytest.raises(ValueError, match="delay"):
        await saq_driver.job("env.probe", delay=-1).dispatch()
    assert saq_driver.queue.recorded == []


def test_invalid_job_params_are_refused_at_decoration():
    with pytest.raises(ValueError, match="retries"):
        Job(retries=0)
    with pytest.raises(ValueError, match="timeout"):
        Job(timeout=0)
    with pytest.raises(ValueError, match="backoff"):
        Job(backoff=-1)


async def test_saq_job_status_reads_the_queue(saq_driver):
    from saq.job import Status

    class _Job:
        status = Status.COMPLETE

    async def _found(key):
        return _Job()

    saq_driver.queue.job = _found  # type: ignore[method-assign]
    assert await saq_driver.job_status("k") == "complete"

    async def _missing(key):
        return None

    saq_driver.queue.job = _missing  # type: ignore[method-assign]
    assert await saq_driver.job_status("missing") is None


# ---------------------------------------------------------------------------
# memory driver — the same envelope, applied by run_pending
# ---------------------------------------------------------------------------


async def test_memory_envelope_retries_a_failed_job():
    attempts = []

    @Job(name="mem.boom")
    async def boom() -> None:
        attempts.append(True)
        raise RuntimeError("kaput")

    mem = MemoryQueue()
    handle = await mem.job("mem.boom", retries=3).dispatch()

    executed = await mem.run_pending()
    assert executed == 1  # one job processed — not one attempt
    assert len(attempts) == 3  # the envelope's full retry budget was spent
    assert len(mem.failures) == 1  # recorded once, after the final attempt
    assert handle.status == "failed"


async def test_memory_envelope_succeeds_on_a_later_attempt():
    attempts = []

    @Job(name="mem.flaky")
    async def flaky() -> None:
        attempts.append(True)
        if len(attempts) < 2:
            raise RuntimeError("transient")

    mem = MemoryQueue()
    handle = await mem.job("mem.flaky", retries=3).dispatch()

    await mem.run_pending()
    assert len(attempts) == 2  # failed once, succeeded on the retry
    assert mem.failures == []
    assert handle.status == "completed"


async def test_memory_envelope_timeout_cuts_a_hung_job():
    from fastplace.queue_failures import reset_failed_job_store

    reset_failed_job_store()

    @Job(name="mem.hung")
    async def hung() -> None:
        await asyncio.sleep(30)

    mem = MemoryQueue()
    started = time.monotonic()
    await mem.job("mem.hung", retries=1, timeout=0.05).dispatch()
    await mem.run_pending()

    assert time.monotonic() - started < 5  # cut at the envelope, not 30s
    assert len(mem.failures) == 1
    assert isinstance(mem.failures[0].error, asyncio.TimeoutError)


async def test_memory_envelope_backoff_sleeps_between_attempts():
    attempts = []

    @Job(name="mem.backoff")
    async def always_boom() -> None:
        attempts.append(True)
        raise RuntimeError("kaput")

    mem = MemoryQueue()
    started = time.monotonic()
    await mem.job("mem.backoff", retries=3, backoff=0.02).dispatch()
    await mem.run_pending()

    elapsed = time.monotonic() - started
    # 0.02 after attempt 1, 0.04 after attempt 2 — the same exponential the
    # saq driver computes from retry_delay/retry_backoff.
    assert elapsed >= 0.05
    assert len(attempts) == 3


async def test_memory_envelope_delay_defers_until_due():
    ran = []

    @Job(name="mem.later")
    async def later() -> None:
        ran.append(True)

    mem = MemoryQueue()
    await mem.job("mem.later", delay=0.05).dispatch()

    executed = await mem.run_pending()  # not due yet
    assert executed == 0
    assert ran == []
    assert await mem.queue_depth() == 1  # still pending, not dropped

    await asyncio.sleep(0.06)
    executed = await mem.run_pending()
    assert executed == 1
    assert ran == [True]


async def test_memory_unique_suppresses_a_duplicate_dispatch():
    @Job(name="mem.unique")
    async def once(user_id: int = 0) -> None:
        return None

    mem = MemoryQueue()
    first = await mem.job("mem.unique", unique=True).dispatch(user_id=1)
    duplicate = await mem.job("mem.unique", unique=True).dispatch(user_id=1)

    assert first is not None
    assert duplicate is None  # an unresolved identical job is suppressed
    assert await mem.queue_depth() == 1

    await mem.run_pending()
    again = await mem.job("mem.unique", unique=True).dispatch(user_id=1)
    assert again is not None  # completed → the identity is free again


async def test_memory_builder_rejects_queue_routing():
    @Job(name="mem.routed")
    async def routed() -> None:
        return None

    mem = MemoryQueue()
    with pytest.raises(ValueError, match="single in-process queue"):
        await mem.job("mem.routed", queue="emails").dispatch()


async def test_memory_dispatch_returns_a_trackable_handle():
    from fastplace.queue_failures import reset_failed_job_store

    reset_failed_job_store()

    @Job(name="mem.h1")
    async def good() -> None:
        return None

    @Job(name="mem.h2")
    async def bad() -> None:
        raise RuntimeError("kaput")

    mem = MemoryQueue()
    queued = await mem.dispatch("mem.h1")
    failing = await mem.dispatch("mem.h2")

    assert queued.status == "queued"
    assert await mem.job_status(queued.key) == "queued"

    await mem.run_pending()
    assert queued.status == "completed"
    assert failing.status == "failed"
    assert await mem.job_status(queued.key) == "completed"
    assert await mem.job_status("no-such-key") is None


async def test_memory_unique_frees_the_key_when_cleared():
    @Job(name="mem.uc")
    async def cleared(user_id: int = 0) -> None:
        return None

    mem = MemoryQueue()
    await mem.job("mem.uc", unique=True).dispatch(user_id=2)
    await mem.clear()

    again = await mem.job("mem.uc", unique=True).dispatch(user_id=2)
    assert again is not None  # cleared jobs release their unique identity


# ---------------------------------------------------------------------------
# driver surface — both drivers speak the builder + lookup
# ---------------------------------------------------------------------------


def test_both_drivers_expose_the_builder_and_lookup():
    assert hasattr(MemoryQueue(), "job")
    assert hasattr(MemoryQueue(), "job_status")
    assert hasattr(SaqQueue(), "job")
    assert hasattr(SaqQueue(), "job_status")
