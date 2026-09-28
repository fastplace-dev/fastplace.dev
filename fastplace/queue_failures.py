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
    String,
    Table,
    Text,
    delete,
    insert,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger("fastplace.queue")

# Framework-owned metadata (the sessions/cache-table pattern): failed-job
# bookkeeping is queue infrastructure, not app domain — it lives off
# Model.metadata so app Alembic revisions never depend on it, and the store
# creates it idempotently on first use.
_failed_jobs_metadata = MetaData()

#: The persisted ledger itself. ``kwargs`` keeps the dispatch payload so
#: ``queue:retry`` can re-dispatch exactly what failed. ``job_key`` carries
#: the queue-side job identity (the saq Job key) so the aborted-job scan can
#: dedupe — a NULL means the row came from a path with no such identity.
#: ``sqlite_autoincrement`` keeps ids monotonic even after deletes, so a
#: retried-and-forgotten failure never resurfaces under a recycled id.
_failed_jobs_table = Table(
    "_fastplace_failed_jobs",
    _failed_jobs_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("kwargs", JSON, nullable=False),
    Column("error", Text, nullable=False),
    Column("failed_at", DateTime, nullable=False, index=True),  # naive UTC
    # Unique: two workers scanning the same ABORTED job must not both ledger
    # it — the index makes the dedupe airtight at the database level.
    # VARCHAR(191), not TEXT: MySQL refuses an index over TEXT without a
    # prefix length (error 1170), and 191 chars stays under the utf8mb4
    # InnoDB index byte limit on every configuration. Job keys are uuid-scale
    # strings — the bound is far above anything real.
    Column("job_key", String(191), nullable=True, index=True, unique=True),
    sqlite_autoincrement=True,
)


def utcnow() -> datetime:
    """Naive UTC now — the convention every ``failed_at`` value follows."""
    return datetime.now(UTC).replace(tzinfo=None)


#: Index name the unique job_key index carries — the upgrade path checks for
#: it by name (MySQL has no CREATE INDEX IF NOT EXISTS, so existence must be
#: probed before issuing the statement).
_JOB_KEY_INDEX = "ix__fastplace_failed_jobs_job_key"


def _upgrade_statements(dialect_name: str, columns: dict[str, str], indexes: set[str]) -> list[str]:
    """The DDL ``_upgrade_legacy_table`` must issue, decided purely.

    Split out so the MySQL path is testable without a MySQL server: a
    legacy ``job_key TEXT`` column there must be narrowed to VARCHAR(191)
    before the unique index is created (MySQL errors out indexing TEXT
    without a prefix length). SQLite and PostgreSQL index TEXT natively —
    no conversion (SQLite cannot ALTER a column type at all). NULLs stay
    allowed, so legacy rows survive every statement.
    """
    stmts: list[str] = []
    if "job_key" not in columns:
        stmts.append(f"ALTER TABLE {_failed_jobs_table.name} ADD COLUMN job_key VARCHAR(191)")
    elif dialect_name == "mysql" and columns.get("job_key", "").upper() == "TEXT":
        stmts.append(f"ALTER TABLE {_failed_jobs_table.name} MODIFY COLUMN job_key VARCHAR(191)")
    if _JOB_KEY_INDEX not in indexes:
        stmts.append(f"CREATE UNIQUE INDEX {_JOB_KEY_INDEX} ON {_failed_jobs_table.name} (job_key)")
    return stmts


def _upgrade_legacy_table(sync_conn: Any) -> None:
    """Bring a pre-``job_key`` table up to the current shape, in place.

    Runs inside ensure_table's transaction on every first use: when the
    column already exists (fresh create or prior upgrade) the inspector
    finds it and nothing is issued. The statements come from
    :func:`_upgrade_statements` — dialect-aware, ANSI where possible.
    """
    from sqlalchemy import inspect

    inspector = inspect(sync_conn)
    if not inspector.has_table(_failed_jobs_table.name):
        return  # create_all just made the full-shape table — nothing legacy
    columns = {
        column["name"]: str(column["type"])
        for column in inspector.get_columns(_failed_jobs_table.name)
    }
    indexes = {index["name"] for index in inspector.get_indexes(_failed_jobs_table.name)}
    for statement in _upgrade_statements(sync_conn.dialect.name, columns, indexes):
        sync_conn.execute(text(statement))


def format_error(exc: BaseException) -> str:
    """One stable error string for both drivers.

    The type name carries the signal when the message alone is empty
    (``RuntimeError`` raising with no text is common in handlers).
    """
    return f"{type(exc).__name__}: {exc}"


def _is_duplicate_ddl(exc: BaseException) -> bool:
    """True when ``exc`` says the schema object a racing peer already made.

    Two worker processes hitting their first failure record at once both run
    ``ensure_table``; the loser's DDL fails with the dialect's flavor of
    "the table/column/index already exists" (sqlite/PostgreSQL phrase it one
    way, MySQL numbers it 1050/1060/1061). That failure is success — the
    object exists, which is all the idempotent ensure promises. Every other
    error stays an error.
    """
    message = f"{exc}".lower()
    return any(
        marker in message
        for marker in (
            "already exists",
            "duplicate column name",
            "duplicate key name",
        )
    )


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
        """Create the table on first use; upgrade a legacy table in place.

        Fresh installs get the full shape from ``create_all(checkfirst=True)``.
        A deployment whose table predates the ``job_key`` column is upgraded
        under this same idempotent call — inspect, ``ALTER TABLE ADD COLUMN``,
        create the index — so no migration step is owed. The column must be
        nullable: legacy rows and failure paths without a queue-side identity
        have nothing to store there.
        """
        if self._ensured:
            return

        def create(sync_conn: Any) -> None:
            _failed_jobs_metadata.create_all(
                sync_conn, tables=[_failed_jobs_table], checkfirst=True
            )
            _upgrade_legacy_table(sync_conn)

        try:
            async with self._engine().begin() as conn:
                await conn.run_sync(create)
        except Exception as exc:  # noqa: BLE001 — only duplicate DDL is benign
            if not _is_duplicate_ddl(exc):
                raise
            # A racing peer (another worker process on its own first failure
            # record) created the same table/column/index a heartbeat earlier
            # — the loser of that race used to crash its first record.
            logger.info("failed-job table already created by a racing peer: %s", exc)
        self._ensured = True

    async def record(
        self, name: str, kwargs: dict[str, Any], error: str, job_key: str | None = None
    ) -> int:
        """Persist one failure; returns the new record's id.

        ``job_key`` (optional) is the queue-side identity — the saq Job key —
        used by :meth:`record_once` to dedupe repeated observations of the
        same lost job.
        """
        await self.ensure_table()
        async with self._engine().begin() as conn:
            result = await conn.execute(
                insert(_failed_jobs_table).values(
                    name=name, kwargs=kwargs, error=error, failed_at=utcnow(), job_key=job_key
                )
            )
        return int(result.inserted_primary_key[0])

    async def record_once(
        self, name: str, kwargs: dict[str, Any], error: str, job_key: str
    ) -> int | None:
        """Record a failure exactly once per ``job_key``.

        The aborted-job scan re-observes the same ABORTED job on every pass
        until its redis TTL expires; without this guard each pass would
        re-record it. Returns the new id, or ``None`` when the key already
        has a row. A concurrent insert racing past the select lands on the
        unique index and is treated as "already recorded" — the dedupe holds
        under multiple workers.
        """
        await self.ensure_table()
        stmt = select(_failed_jobs_table.c.id).where(_failed_jobs_table.c.job_key == job_key)
        async with self._engine().connect() as conn:
            existing = (await conn.execute(stmt)).first()
        if existing is not None:
            return None
        try:
            return await self.record(name, kwargs, error, job_key=job_key)
        except IntegrityError:
            return None  # a racing scan recorded it first — same conclusion

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


async def record_failure_once(
    name: str, kwargs: dict[str, Any], error: str, job_key: str
) -> int | None:
    """Best-effort deduped recording for the aborted-job scan.

    Same isolation contract as :func:`record_failure` — the scan is a
    companion, never a load-bearing wall: a store outage logs and the
    worker keeps working (the job stays visible in redis either way).
    """
    try:
        return await failed_job_store().record_once(name, kwargs, error, job_key)
    except Exception:  # noqa: BLE001 — isolation is the contract
        logger.warning("could not persist aborted-job record for '%s'", name, exc_info=True)
        return None
