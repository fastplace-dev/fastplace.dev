"""Job correlation — log records from inside jobs carry job_id (plat-G9).

Both fastplace-owned execution paths are hooked: the memory driver's
``_run_one`` and the saq adapter wrapper. saq's own internals are
third-party — the adapter is the single seam every production job passes.
"""

from __future__ import annotations

import logging

import pytest

from fastplace.logging.context import get_job_id
from fastplace.queue import Job, MemoryQueue, _adapt_sa_handler, reset_queue, reset_registry


@pytest.fixture(autouse=True)
def _fresh_queue():
    reset_registry()
    reset_queue()
    yield
    reset_registry()
    reset_queue()


async def test_memory_job_records_correlation(capture_records):
    observed: dict = {}

    @Job()
    async def correlate_me():
        observed["job_id"] = get_job_id()
        logging.getLogger("fastplace.test.job").info("inside the job")

    memory = MemoryQueue()
    await memory.dispatch("correlate_me")
    executed = await memory.run_pending()
    assert executed == 1
    assert observed["job_id"] == "correlate_me"
    assert any("job_id=correlate_me" in line for line in capture_records.lines)


async def test_memory_job_keeps_id_across_retries(capture_records):
    attempts = 0

    @Job(retries=2)
    async def flaky():
        nonlocal attempts
        attempts += 1
        logging.getLogger("fastplace.test.job").info("attempt %d", attempts)
        if attempts < 2:
            raise RuntimeError("not yet")

    memory = MemoryQueue()
    await memory.dispatch("flaky")
    await memory.run_pending()
    assert attempts == 2
    # Both the failed attempt and the retry carry the same job_id.
    assert sum("job_id=flaky" in line for line in capture_records.lines) == 2


async def test_saq_adapter_correlates_by_registered_name(capture_records):
    observed: dict = {}

    async def handler(**kwargs):
        observed["job_id"] = get_job_id()
        logging.getLogger("fastplace.test.job").info("saq side")

    wrapped = _adapt_sa_handler(handler)
    await wrapped({"name": "saq_job_name"})
    assert observed["job_id"] == "saq_job_name"
    assert any("job_id=saq_job_name" in line for line in capture_records.lines)


async def test_saq_adapter_falls_back_to_function_name(capture_records):
    observed: dict = {}

    async def named_handler(**kwargs):
        observed["job_id"] = get_job_id()

    wrapped = _adapt_sa_handler(named_handler)
    await wrapped({})
    assert observed["job_id"] == "named_handler"


async def test_job_id_cleared_after_run():
    observed: dict = {}

    @Job()
    async def note_id():
        observed["job_id"] = get_job_id()

    memory = MemoryQueue()
    await memory.dispatch("note_id")
    await memory.run_pending()
    assert observed["job_id"] == "note_id"
    # The worker process lives on — the id must not leak into later records.
    assert get_job_id() == ""
