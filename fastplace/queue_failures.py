"""Persisted failed-job ledger — the surface behind the ``queue:failed`` CLI family.

The memory driver already captures exceptions in-process
(``MemoryQueue.failures``), but that ledger dies with the process, and saq
marks jobs failed inside redis where nothing human browses them. Every worker
failure path therefore also records here, and ``fastplace queue:failed`` /
``queue:flush`` / ``queue:forget`` / ``queue:prune-failed`` / ``queue:retry``
read and manage those rows.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    delete,
    insert,
    select,
)

logger = logging.getLogger("fastplace.queue")

# Framework-owned metadata (the sessions/cache-table pattern): failed-job
# bookkeeping is queue infrastructure, not app domain — it lives off
# Model.metadata so app Alembic revisions never depend on it, and the store
# creates it idempotently on first use.
_failed_jobs_metadata = MetaData()

#: The persisted ledger itself. ``kwargs`` keeps the dispatch payload so
#: ``queue:retry`` can re-dispatch exactly what failed.
_failed_jobs_table = Table(
    "_fastplace_failed_jobs",
    _failed_jobs_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("kwargs", JSON, nullable=False),
    Column("error", Text, nullable=False),
    Column("failed_at", DateTime, nullable=False, index=True),  # naive UTC
)


def utcnow() -> datetime:
    """Naive UTC now — the convention every ``failed_at`` value follows."""
    return datetime.now(UTC).replace(tzinfo=None)


def format_error(exc: BaseException) -> str:
    """One stable error string for both drivers.

    The type name carries the signal when the message alone is empty
    (``RuntimeError`` raising with no text is common in handlers).
    """
    return f"{type(exc).__name__}: {exc}"


class FailedJobStore:
    """SQLAlchemy Core ledger over ``_fastplace_failed_jobs``.

    Portable across SQLite/PG/MySQL (Core statements only, no dialect-specific
    syntax), mirroring the DatabaseCache driver: lazy idempotent table
    creation, one statement per operation.
    """

    def __init__(self) -> None:
        self._ensured = False

    def _engine(self) -> Any:
        # Function-level import: reset_db() rebinds fastplace.db.db, and every
        # call must see the live binding (tests reload config per case).
        from fastplace.db import db

        return db.manager.engine("default")

    async def ensure_table(self) -> None:
        """Create the table on first use (checkfirst — idempotent)."""
        if self._ensured:
            return

        def create(sync_conn: Any) -> None:
            _failed_jobs_metadata.create_all(
                sync_conn, tables=[_failed_jobs_table], checkfirst=True
            )

        async with self._engine().begin() as conn:
            await conn.run_sync(create)
        self._ensured = True

    async def record(self, name: str, kwargs: dict[str, Any], error: str) -> int:
        """Persist one failure; returns the new record's id."""
        await self.ensure_table()
        async with self._engine().begin() as conn:
            result = await conn.execute(
                insert(_failed_jobs_table).values(
                    name=name, kwargs=kwargs, error=error, failed_at=utcnow()
                )
            )
        return int(result.inserted_primary_key[0])

    async def list(self, offset: int = 0, limit: int = 50) -> list[Any]:
        """Newest-first page of failed-job rows."""
        await self.ensure_table()
        stmt = (
            select(_failed_jobs_table)
            .order_by(_failed_jobs_table.c.id.desc())
            .offset(offset)
            .limit(limit)
        )
        async with self._engine().connect() as conn:
            return list((await conn.execute(stmt)).all())

    async def get(self, job_id: int) -> Any | None:
        """One record by id, or None when no such record exists."""
        await self.ensure_table()
        stmt = select(_failed_jobs_table).where(_failed_jobs_table.c.id == job_id)
        async with self._engine().connect() as conn:
            return (await conn.execute(stmt)).first()

    async def delete(self, job_id: int) -> bool:
        """Delete one record; False when the id was already gone."""
        await self.ensure_table()
        async with self._engine().begin() as conn:
            result = await conn.execute(
                delete(_failed_jobs_table).where(_failed_jobs_table.c.id == job_id)
            )
        return int(result.rowcount or 0) > 0

    async def prune(self, before: datetime) -> int:
        """Delete records that failed before ``before``; returns how many."""
        await self.ensure_table()
        async with self._engine().begin() as conn:
            result = await conn.execute(
                delete(_failed_jobs_table).where(_failed_jobs_table.c.failed_at < before)
            )
        return int(result.rowcount or 0)

    async def flush(self) -> int:
        """Delete every record; returns how many."""
        await self.ensure_table()
        async with self._engine().begin() as conn:
            result = await conn.execute(delete(_failed_jobs_table))
        return int(result.rowcount or 0)


_default_store: FailedJobStore | None = None


def failed_job_store() -> FailedJobStore:
    """The process-wide failed-job store."""
    global _default_store
    if _default_store is None:
        _default_store = FailedJobStore()
    return _default_store


def reset_failed_job_store() -> None:
    """Drop the singleton — tests and config reloads."""
    global _default_store
    _default_store = None


async def record_failure(name: str, kwargs: dict[str, Any], error: str) -> int | None:
    """Best-effort recording for the worker failure paths.

    Persistence must never take a drain down with it: a store that cannot
    write (db down, unserializable kwargs) logs and lets the queue continue.
    """
    try:
        return await failed_job_store().record(name, kwargs, error)
    except Exception:  # noqa: BLE001 — isolation is the contract
        logger.warning("could not persist failed-job record for '%s'", name, exc_info=True)
        return None
