"""Query instrumentation — SQL log, slow-query warn, N+1 detection (T7.1).

Every engine the manager creates carries event listeners: statements log at
DEBUG, slow statements WARN, and a repeated identical statement inside one
tracker window (a request) flags the classic N+1 pattern.
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture()
async def db_model(monkeypatch, tmp_path):
    from fastplace.db import reset_db

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/instrumented.db")
    reset_db()

    from fastplace.orm import Field, Model

    class Row(Model):
        __tablename__ = "instrumented_rows"

        id: int = Field(primary_key=True)
        title: str = ""

    from fastplace.db import db

    await db.create_all()
    return Row


async def test_statements_log_at_debug_level(db_model, caplog):
    Row = db_model
    with caplog.at_level(logging.DEBUG, logger="fastplace.db.sql"):
        await Row.create(title="logged")

    sql_lines = [r for r in caplog.records if r.name == "fastplace.db.sql"]
    assert any("INSERT" in r.getMessage() for r in sql_lines)


async def test_slow_queries_warn_past_the_threshold(db_model, monkeypatch, caplog):
    Row = db_model
    monkeypatch.setenv("QUERY_SLOW_MS", "0")  # every statement is "slow"

    with caplog.at_level(logging.WARNING, logger="fastplace.db.sql"):
        await Row.create(title="slow")

    warns = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and "slow" in r.getMessage().lower()
    ]
    assert warns, "expected a slow-query warning with a zero threshold"


async def test_repeated_identical_statements_flag_n_plus_one(db_model, monkeypatch, caplog):
    Row = db_model
    monkeypatch.setenv("QUERY_N1_THRESHOLD", "3")
    from fastplace.orm.instrumentation import activate_tracker

    # N+1 counting is per request — the kernel wraps each request in a window
    with activate_tracker(), caplog.at_level(logging.WARNING, logger="fastplace.db.sql"):
        for index in range(5):
            await Row.where(Row.title == f"row-{index}").first()

    n1 = [r for r in caplog.records if "N+1" in r.getMessage()]
    assert n1, "five identical SELECTs should trip the N+1 warning"


async def test_tracker_counts_statements_and_duplicates(db_model):
    Row = db_model
    from fastplace.orm.instrumentation import activate_tracker, current_stats

    with activate_tracker():
        for index in range(4):
            await Row.where(Row.title == f"dup-{index}").first()
        stats = current_stats()

    assert stats is not None
    assert stats.statements >= 4
    # the repeated SELECT is recorded as a duplicate, not four unrelated runs
    assert sum(stats.duplicates.values()) >= 3


def test_executemany_batches_never_count_as_n1():
    from fastplace.orm.instrumentation import QueryTracker

    tracker = QueryTracker()
    for _ in range(20):
        tracker.record("INSERT INTO t VALUES (?)", 0.001, executemany=True)
    assert tracker.stats.duplicates == {}


def test_stats_snapshot_is_immutable_by_callers():
    from fastplace.orm.instrumentation import QueryTracker

    tracker = QueryTracker()
    tracker.record("SELECT 1", 0.001)
    stats = tracker.stats
    with pytest.raises((AttributeError, TypeError)):
        stats.statements = 99
