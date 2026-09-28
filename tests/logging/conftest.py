"""Isolation for logging tests — logging config is process-global state.

Every test starts from a clean root logger and a clean configured flag, and
every test restores whatever the suite had before. Without this, a handler
pointing at a deleted tmp_path would leak into sibling test files (pytest
runs the whole suite in one process), and an idempotency flag left set would
silently no-op the next test's configure_logging call.
"""

from __future__ import annotations

import logging

import pytest

from fastplace.logging.channels import DEFAULT_FORMAT, CorrelationFormatter, reset_logging_state
from fastplace.logging.context import CorrelationFilter

_ENV_KEYS = (
    "LOG_CHANNEL",
    "LOG_LEVEL",
    "LOG_STACK",
    "LOG_MAX_BYTES",
    "LOG_BACKUP_COUNT",
    "LOG_DAILY_DAYS",
)


@pytest.fixture(autouse=True)
def _clean_logging_state(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_root_level = root.level
    fastplace_logger = logging.getLogger("fastplace")
    saved_fastplace_level = fastplace_logger.level
    # Kernel-booting tests rebind the default config registry to the tmp
    # project (create_app -> reset_config); leave it exactly as we found it.
    import fastplace.config as config_module

    default_config_before = config_module._default_config

    reset_logging_state()
    # INFO passes the root-logger gate: without a configure call, pytest's
    # default root level (WARNING) swallows records before our handlers —
    # correlation assertions need the records, not the default gate.
    root.setLevel(logging.INFO)
    yield

    reset_logging_state()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_root_level)
    fastplace_logger.setLevel(saved_fastplace_level)
    config_module._default_config = default_config_before


class _Capture(logging.Handler):
    """Root-level handler carrying the correlation filter + formatter.

    Records are asserted through the real formatting path — the same one
    production files go through — so a broken filter/formatter pairing fails
    here instead of in a deployed log file.
    """

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.records: list[logging.LogRecord] = []
        self.addFilter(CorrelationFilter())
        self.setFormatter(CorrelationFormatter(DEFAULT_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        self.lines.append(self.format(record))


@pytest.fixture()
def capture_records() -> _Capture:
    handler = _Capture()
    logging.getLogger().addHandler(handler)
    return handler
