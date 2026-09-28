"""Logging — channels, rotation, and request/job correlation (plat-G9).

The framework's observability floor: one call at boot
(:func:`configure_logging`, made by the kernel) puts application logs in
``storage/logs/`` with rotation and retention, and every record carries the
request or job that produced it. Application code just logs::

    import logging

    logger = logging.getLogger("fastplace.app.orders")   # house convention
    logger.info("order %s placed", order_id)             # ids ride along

Channels (``LOG_CHANNEL``): ``single`` (rotating file, the zero-config
default), ``daily`` (midnight files with day retention), ``stderr``,
``null``, and ``stack`` (a composite of ``LOG_STACK``, e.g. files + stderr
for containers). Unknown channels fail the boot loudly — a silent logging
black hole is worse than a failed deploy.
"""

from __future__ import annotations

from fastplace.logging.channels import (
    DEFAULT_FORMAT,
    CorrelationFormatter,
    DailyRotatingFileHandler,
    configure_logging,
    reset_logging_state,
)
from fastplace.logging.context import (
    CorrelationFilter,
    get_job_id,
    get_request_id,
    job_context,
    request_context,
)
from fastplace.logging.middleware import (
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
    sanitize_request_id,
)

__all__ = [
    "DEFAULT_FORMAT",
    "REQUEST_ID_HEADER",
    "CorrelationFilter",
    "CorrelationFormatter",
    "DailyRotatingFileHandler",
    "RequestIdMiddleware",
    "configure_logging",
    "get_job_id",
    "get_request_id",
    "job_context",
    "request_context",
    "reset_logging_state",
    "sanitize_request_id",
]
