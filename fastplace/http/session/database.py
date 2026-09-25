"""Database-backed session store — the production default driver."""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    delete,
    insert,
    select,
    update,
)

from fastplace.http.session.base import StoredSession, session_lifetime

# Framework-owned metadata: the sessions table is infrastructure, not app
# domain — keeping it off Model.metadata means app Alembic revisions never
# depend on framework tables, and the store can create it idempotently.
_metadata = MetaData()

sessions_table = Table(
    "sessions",
    _metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", Integer, nullable=True, index=True),
    Column("payload", Text, nullable=False),
    Column("last_activity", Integer, nullable=False, index=True),
    Column("ip_address", String(45), nullable=True),
    Column("user_agent", String(255), nullable=True),
)


class DatabaseSessionStore:
    """SQLAlchemy Core session store — portable across SQLite/PG/MySQL."""

    def __init__(self) -> None:
        self._ensured = False

    def _engine(self) -> Any:
        from fastplace.db import db

        return db.manager.engine("default")

    async def _ensure_table(self) -> None:
        """Create the table on first use (checkfirst — idempotent)."""
        if self._ensured:
            return

        def create(sync_conn: Any) -> None:
            _metadata.create_all(sync_conn, tables=[sessions_table], checkfirst=True)

        async with self._engine().begin() as conn:
            await conn.run_sync(create)
        self._ensured = True

    async def read(self, session_id: str) -> StoredSession | None:
        await self._ensure_table()
        stmt = select(sessions_table.c.payload, sessions_table.c.last_activity).where(
            sessions_table.c.id == session_id
        )
        async with self._engine().connect() as conn:
            row = (await conn.execute(stmt)).first()
        if row is None:
            return None
        last_activity = int(row.last_activity)
        # Lazy expiry: a row past the window reads as missing even before GC.
        if time.time() - last_activity >= session_lifetime():
            return None
        return StoredSession(payload=json.loads(row.payload or "{}"), last_activity=last_activity)

    async def write(
        self,
        session_id: str,
        payload: dict[str, Any],
        *,
        user_id: int | None = None,
    ) -> None:
        await self._ensure_table()
        now = int(time.time())
        encoded = json.dumps(payload)
        # Portable upsert (SQLite/PG/MySQL): UPDATE first, INSERT when the
        # row is absent — no dialect-specific ON CONFLICT syntax. Concurrent
        # first-writes of one ID cannot happen: only the middleware writes,
        # once per request, on a 128-bit random ID it minted itself.
        async with self._engine().begin() as conn:
            result = await conn.execute(
                update(sessions_table)
                .where(sessions_table.c.id == session_id)
                .values(payload=encoded, user_id=user_id, last_activity=now)
            )
            if result.rowcount == 0:
                await conn.execute(
                    insert(sessions_table).values(
                        id=session_id,
                        user_id=user_id,
                        payload=encoded,
                        last_activity=now,
                    )
                )

    async def destroy(self, session_id: str) -> None:
        await self._ensure_table()
        async with self._engine().begin() as conn:
            await conn.execute(delete(sessions_table).where(sessions_table.c.id == session_id))

    async def destroy_for_user(self, user_id: Any, *, except_session_id: str | None = None) -> int:
        """Remove every session row attributed to ``user_id`` (logout-others)."""
        await self._ensure_table()
        stmt = delete(sessions_table).where(sessions_table.c.user_id == user_id)
        if except_session_id is not None:
            stmt = stmt.where(sessions_table.c.id != except_session_id)
        async with self._engine().begin() as conn:
            result = await conn.execute(stmt)
            return int(result.rowcount or 0)

    async def sessions_for_user(self, user_id: Any) -> list[Any]:
        """The user's ACTIVE session rows, newest activity first.

        Read-only enumeration for ``auth:sessions`` — mirrors read()'s lazy
        expiry: a row past the window is listed as missing even before GC.
        ip_address/user_agent stay NULL today (the middleware does not write
        them); the caller renders "—".
        """
        await self._ensure_table()
        threshold = int(time.time()) - session_lifetime()
        stmt = (
            select(
                sessions_table.c.id,
                sessions_table.c.last_activity,
                sessions_table.c.ip_address,
                sessions_table.c.user_agent,
            )
            .where(sessions_table.c.user_id == user_id)
            .where(sessions_table.c.last_activity >= threshold)
            .order_by(sessions_table.c.last_activity.desc())
        )
        async with self._engine().connect() as conn:
            return list((await conn.execute(stmt)).all())

    async def gc(self, lifetime: int | None = None) -> int:
        await self._ensure_table()
        window = lifetime if lifetime is not None else session_lifetime()
        threshold = int(time.time()) - window
        async with self._engine().begin() as conn:
            result = await conn.execute(
                delete(sessions_table).where(sessions_table.c.last_activity < threshold)
            )
            return int(result.rowcount or 0)
