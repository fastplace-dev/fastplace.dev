"""Query instrumentation — SQL log, slow-query warnings, N+1 detection.

Every engine the manager creates is wired here (blueprint Phase 7):
statements log at DEBUG on ``fastplace.db.sql``, statements slower than
``QUERY_SLOW_MS`` (default 250ms) WARN, and a statement repeating more
than ``QUERY_N1_THRESHOLD`` times (default 5) inside one tracker window
flags the classic N+1 pattern. ``activate_tracker()`` scopes a window —
the HTTP kernel opens one per request.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger("fastplace.db.sql")

_current_tracker: ContextVar[QueryTracker | None] = ContextVar(
    "fastplace_query_tracker", default=None
)


@dataclass(frozen=True)
class QueryStats:
    """Immutable snapshot of one tracker window."""

    statements: int = 0
    slow_queries: int = 0
    total_seconds: float = 0.0
    duplicates: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        """JSON-safe digest for debug payloads — statement count, slow count,
        cumulative time, and the repeated statements (N+1 candidates) with
        whitespace-collapsed previews so payloads stay bounded."""
        previews: dict[str, int] = {}
        for sql, count in self.duplicates.items():
            # Distinct statements can collapse onto one preview; merge their
            # counts so nothing is silently dropped.
            preview = " ".join(sql.split())[:120]
            previews[preview] = previews.get(preview, 0) + count
        return {
            "statements": self.statements,
            "slow_queries": self.slow_queries,
            "total_seconds": round(self.total_seconds, 4),
            "duplicates": previews,
        }


class QueryTracker:
    """Counts statements inside one window (usually a request)."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._statements = 0
        self._slow = 0
        self._total = 0.0
        self._n1_flagged: set[str] = set()

    def record(self, statement: str, seconds: float, *, executemany: bool = False) -> None:
        """Record one executed statement; executemany batches are not N+1."""
        self._statements += 1
        self._total += seconds
        if executemany:
            return
        count = self._counts.get(statement, 0) + 1
        self._counts[statement] = count
        if count > 1:
            self._on_duplicate(statement, count)

    def _on_duplicate(self, statement: str, count: int) -> None:
        from fastplace.config import config

        threshold = _as_int(config("QUERY_N1_THRESHOLD", default=5), 5)
        if count == threshold and statement not in self._n1_flagged:
            self._n1_flagged.add(statement)
            preview = " ".join(statement.split())[:120]
            logger.warning(
                "possible N+1: %r executed %d times in this request — batch it, "
                "use with_() eager loading, or filter in the database",
                preview,
                count,
            )

    @property
    def stats(self) -> QueryStats:
        return QueryStats(
            statements=self._statements,
            slow_queries=self._slow,
            total_seconds=self._total,
            duplicates={sql: n for sql, n in self._counts.items() if n > 1},
        )


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


@contextmanager
def activate_tracker() -> Iterator[QueryTracker]:
    """Scope a counting window — one HTTP request, one job, one command."""
    tracker = QueryTracker()
    token = _current_tracker.set(tracker)
    try:
        yield tracker
    finally:
        _current_tracker.reset(token)


def current_stats() -> QueryStats | None:
    """Snapshot of the active window (None outside one)."""
    tracker = _current_tracker.get()
    return tracker.stats if tracker is not None else None


def request_query_stats(request: Any) -> QueryStats | None:
    """Stats for the request being handled.

    During normal handling the live tracker window answers. When an exception
    escaped the stack, the kernel's tracker middleware has already closed the
    window — in that case the snapshot it stashed on the ASGI scope answers
    (the error handler runs outside the window).
    """
    tracker = _current_tracker.get()
    if tracker is not None:
        return tracker.stats
    scope = getattr(request, "scope", None)
    if isinstance(scope, dict):
        stats = scope.get("fastplace_query_stats")
        if isinstance(stats, QueryStats):
            return stats
    return None


def install_instrumentation(engine: AsyncEngine) -> None:
    """Attach SQL logging/warning listeners to an async engine."""

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        context._fastplace_query_start = time.perf_counter()  # type: ignore[attr-defined]

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        started = getattr(context, "_fastplace_query_start", None)
        seconds = max(0.0, time.perf_counter() - started) if started else 0.0

        logger.debug("sql %.1fms %s", seconds * 1000, " ".join(statement.split())[:120])

        from fastplace.config import config

        threshold_ms = _as_int(config("QUERY_SLOW_MS", default=250), 250)
        slow = seconds * 1000 >= threshold_ms
        if slow:
            logger.warning(
                "slow query: %dms exceeds %dms threshold — %r",
                int(seconds * 1000),
                threshold_ms,
                " ".join(statement.split())[:120],
            )

        tracker = _current_tracker.get()
        if tracker is not None:
            if slow:
                tracker._slow += 1
            tracker.record(statement, seconds, executemany=bool(executemany))
