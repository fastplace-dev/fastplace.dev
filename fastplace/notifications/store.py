"""The notifications table — framework-owned storage for in-app notifications.

Infrastructure, not app domain: a dedicated MetaData off the app model
metadata so app Alembic revisions never depend on it, created idempotently
on first use with ``checkfirst=True`` (the cache-table pattern). Payloads
store as JSON text — the channel contract already promises JSON-safe dicts.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
    Text,
    insert,
    select,
    update,
)

_notifications_metadata = MetaData()

_notifications_table = Table(
    "notifications",
    _notifications_metadata,
    Column("id", String(36), primary_key=True),
    Column("notifiable_type", String(255), nullable=False),
    Column("notifiable_id", String(255), nullable=False),
    Column("payload", Text, nullable=False),
    # NULL until the recipient reads it — the unread badge is an index miss away
    Column("read_at", DateTime, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Index("ix_notifications_notifiable", "notifiable_type", "notifiable_id"),
)


def _utc_now() -> datetime:
    """Naive UTC — one clock convention across SQLite/PG/MySQL."""
    return datetime.now(UTC).replace(tzinfo=None)


class NotificationStore:
    """Insert / read / mark-read over the framework-owned notifications table."""

    def __init__(self) -> None:
        self._ensured = False

    def _engine(self) -> Any:
        # Function-level import: reset_db() rebinds fastplace.db.db, and every
        # call must see the live binding (tests reload config per case).
        from fastplace.db import db

        return db.manager.engine("default")

    async def _ensure_table(self) -> None:
        """Create the table on first use (checkfirst — idempotent)."""
        if self._ensured:
            return

        def create(sync_conn: Any) -> None:
            _notifications_metadata.create_all(
                sync_conn, tables=[_notifications_table], checkfirst=True
            )

        async with self._engine().begin() as conn:
            await conn.run_sync(create)
        self._ensured = True

    @staticmethod
    def _row_dict(row: Any) -> dict[str, Any]:
        """One stored row as a JSON-safe dict (payload decoded, ISO stamps)."""
        return {
            "id": row.id,
            "notifiable_type": row.notifiable_type,
            "notifiable_id": row.notifiable_id,
            "payload": json.loads(row.payload),
            "read_at": row.read_at.isoformat() if row.read_at is not None else None,
            "created_at": row.created_at.isoformat(),
        }

    async def record(
        self, *, notifiable_type: str, notifiable_id: str, payload: dict
    ) -> dict[str, Any]:
        """Insert one notification row; returns the stored dict as handed back."""
        await self._ensure_table()
        created_at = _utc_now()
        row_id = uuid.uuid4().hex
        async with self._engine().begin() as conn:
            await conn.execute(
                insert(_notifications_table).values(
                    id=row_id,
                    notifiable_type=notifiable_type,
                    notifiable_id=notifiable_id,
                    payload=json.dumps(payload),
                    read_at=None,
                    created_at=created_at,
                )
            )
        return {
            "id": row_id,
            "notifiable_type": notifiable_type,
            "notifiable_id": notifiable_id,
            "payload": payload,
            "read_at": None,
            "created_at": created_at.isoformat(),
        }

    async def mark_read(
        self,
        notification_id: str,
        *,
        notifiable_type: str | None = None,
        notifiable_id: str | None = None,
    ) -> bool:
        """Stamp read_at (UTC) on an unread row; True when a row flipped.

        Idempotent by construction — the UPDATE only matches unread rows, so
        a second call on the same id is a no-op returning False. Pass
        ``notifiable_type``/``notifiable_id`` to scope the flip to one
        owner: with a scope, a client-supplied id cannot mark another
        notifiable's rows.
        """
        await self._ensure_table()
        conditions = [
            _notifications_table.c.id == notification_id,
            _notifications_table.c.read_at.is_(None),
        ]
        if notifiable_type is not None:
            conditions.append(_notifications_table.c.notifiable_type == notifiable_type)
        if notifiable_id is not None:
            conditions.append(_notifications_table.c.notifiable_id == notifiable_id)
        async with self._engine().begin() as conn:
            result = await conn.execute(
                update(_notifications_table).where(*conditions).values(read_at=_utc_now())
            )
        return int(result.rowcount or 0) > 0

    async def read(
        self, *, notifiable_type: str, notifiable_id: str, unread_only: bool = True
    ) -> list[dict[str, Any]]:
        """The notifiable's notifications, newest first (created_at desc)."""
        await self._ensure_table()
        stmt = (
            select(_notifications_table)
            .where(_notifications_table.c.notifiable_type == notifiable_type)
            .where(_notifications_table.c.notifiable_id == notifiable_id)
            .order_by(_notifications_table.c.created_at.desc(), _notifications_table.c.id.desc())
        )
        if unread_only:
            stmt = stmt.where(_notifications_table.c.read_at.is_(None))
        async with self._engine().connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [self._row_dict(row) for row in rows]
