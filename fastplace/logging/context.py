"""Correlation context — request and job ids riding every log record.

Structured debugging needs one question answered first: *which request/job
produced this line?* The ids live in contextvars (async-task-local by
design — concurrent requests never see each other's ids) and reach the
records through a logging filter, so application code never passes them by
hand: a controller logs the same way a job does, and the id is simply there.

The formatter requires ``request_id``/``job_id`` record attributes; the
filter (installed on every handler we configure) guarantees them, empty
when the record originates outside any request or job.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("fastplace_request_id", default="")
_job_id: ContextVar[str] = ContextVar("fastplace_job_id", default="")


@contextmanager
def request_context(request_id: str) -> Iterator[str]:
    """Scope a request id — the RequestIdMiddleware sets it per request."""
    token = _request_id.set(request_id)
    try:
        yield request_id
    finally:
        _request_id.reset(token)


@contextmanager
def job_context(job_id: str) -> Iterator[str]:
    """Scope a job id — the queue drivers set it around each execution."""
    token = _job_id.set(job_id)
    try:
        yield job_id
    finally:
        _job_id.reset(token)


def get_request_id() -> str:
    """The current request id, or ``""`` outside any request."""
    return _request_id.get()


def get_job_id() -> str:
    """The current job id, or ``""`` outside any job execution."""
    return _job_id.get()


class CorrelationFilter(logging.Filter):
    """Inject ``request_id``/``job_id`` onto every record passing the handler.

    Handler-level placement (not logger-level): third-party loggers
    propagating to the root get the fields too, without touching their
    logger objects.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        record.job_id = _job_id.get()
        return True
