"""Correlation context — request/job ids on log records (plat-G9)."""

from __future__ import annotations

import logging

from fastplace.logging.context import (
    CorrelationFilter,
    get_job_id,
    get_request_id,
    job_context,
    request_context,
)

logger = logging.getLogger("fastplace.test.context")


def test_request_context_sets_and_resets():
    assert get_request_id() == ""
    with request_context("abc123"):
        assert get_request_id() == "abc123"
    assert get_request_id() == ""


def test_job_context_sets_and_resets():
    assert get_job_id() == ""
    with job_context("sync_catalog"):
        assert get_job_id() == "sync_catalog"
    assert get_job_id() == ""


def test_contexts_nest_and_compose():
    with request_context("outer"):
        with request_context("inner"):
            with job_context("job1"):
                assert (get_request_id(), get_job_id()) == ("inner", "job1")
            assert (get_request_id(), get_job_id()) == ("inner", "")
        assert (get_request_id(), get_job_id()) == ("outer", "")


def test_context_survives_exception():
    try:
        with request_context("doomed"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert get_request_id() == ""


def test_filter_injects_current_ids():
    record = logger.makeRecord(
        "fastplace.test.context", logging.INFO, __file__, 1, "msg", None, None
    )
    with request_context("rid-9"), job_context("job-9"):
        assert CorrelationFilter().filter(record) is True
    assert record.request_id == "rid-9"
    assert record.job_id == "job-9"


def test_filter_defaults_empty_without_context():
    record = logger.makeRecord(
        "fastplace.test.context", logging.INFO, __file__, 1, "msg", None, None
    )
    CorrelationFilter().filter(record)
    assert record.request_id == ""
    assert record.job_id == ""
