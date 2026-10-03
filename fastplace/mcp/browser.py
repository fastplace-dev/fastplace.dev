"""Browser log capture — in-memory ring buffer behind ``browser-logs``.

The HTTP route (``/_fastplace/mcp/browser-logs``) accepts JSON console/error
reports from the browser-capture script and appends them here; the MCP tool
drains the tail. One buffer per server process, bounded by ``cap``.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

_DEFAULT_CAP = 1000

_buffer: BrowserLogBuffer | None = None
_lock = threading.Lock()


class BrowserLogBuffer:
    """Thread-safe bounded FIFO of browser-side log entries."""

    def __init__(self, cap: int = _DEFAULT_CAP) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=cap)
        self._lock = threading.Lock()

    def add(self, entry: dict[str, Any]) -> None:
        record = dict(entry)
        record.setdefault("timestamp", time.time())
        with self._lock:
            self._entries.append(record)

    def tail(self, count: int) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._entries)
        return entries[-count:] if count > 0 else []

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def get_buffer() -> BrowserLogBuffer:
    """Process-wide buffer (created on first use)."""
    global _buffer
    if _buffer is None:
        with _lock:
            if _buffer is None:
                _buffer = BrowserLogBuffer()
    return _buffer


def reset_buffer() -> None:
    """Drop the process-wide buffer (tests)."""
    global _buffer
    with _lock:
        _buffer = None
