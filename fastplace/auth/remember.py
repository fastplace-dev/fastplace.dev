"""Remember-me tokens — rotating selector|validator pairs (spec §4.4).

The browser cookie carries ``row_id|validator``; only ``sha256(validator)``
is stored. Every successful authentication rotates the pair (the old row is
deleted and a fresh one inserted atomically), so a stolen cookie stops working
the moment the real user visits, and a database leak cannot forge cookies.

Framework-owned table: ``remember_tokens`` lives on dedicated metadata (the
sessions-table pattern), is created idempotently by this store, and never
appears in app Alembic revisions.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    delete,
    insert,
    select,
)

# Framework-owned metadata — the remember_tokens table is infrastructure,
# never an app Alembic revision (the sessions-table pattern).
_metadata = MetaData()

remember_tokens = Table(
    "remember_tokens",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("created_at", Integer, nullable=False),
    Column("last_used_at", Integer, nullable=True),
)

REMEMBER_COOKIE_NAME = "fastplace_remember"
REMEMBER_COOKIE_TTL = 400 * 24 * 3600  # seconds — the "remember me" horizon
# ASGI-scope marker the guard sets and the auth middleware flushes as a
# Set-Cookie header on the way out (guards never touch responses directly).
REMEMBER_COOKIE_SCOPE = "fastplace_remember_cookie"
# Scope flag: this request authenticated via the remember cookie, not the
# session (guards/middleware may treat it with extra suspicion).
VIA_REMEMBER_SCOPE = "fastplace_via_remember"


def _hash_validator(validator: str) -> str:
    return hashlib.sha256(validator.encode("utf-8")).hexdigest()


class RememberTokenStore:
    """SQLAlchemy Core token store — portable across SQLite/PG/MySQL."""

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
            _metadata.create_all(sync_conn, tables=[remember_tokens], checkfirst=True)

        async with self._engine().begin() as conn:
            await conn.run_sync(create)
        self._ensured = True

    async def _insert_pair(self, user_id: Any, *, last_used_at: int | None) -> str:
        """Insert one row (fresh validator); returns the raw cookie value."""
        validator = secrets.token_urlsafe(48)
        async with self._engine().begin() as conn:
            result = await conn.execute(
                insert(remember_tokens).values(
                    user_id=user_id,
                    token_hash=_hash_validator(validator),
                    created_at=int(time.time()),
                    last_used_at=last_used_at,
                )
            )
            row_id = result.inserted_primary_key[0]
        return f"{row_id}|{validator}"

    async def issue(self, user_id: Any) -> str:
        """Mint a fresh pair for ``user_id``; returns the raw cookie value."""
        await self._ensure_table()
        return await self._insert_pair(user_id, last_used_at=None)

    async def consume(self, cookie: str) -> tuple[Any, str] | None:
        """Validate + rotate. Returns ``(user_id, fresh_cookie)`` or None."""
        await self._ensure_table()
        selector, sep, validator = cookie.partition("|")
        if not sep or not selector.isdigit() or not validator:
            return None
        stmt = select(
            remember_tokens.c.id,
            remember_tokens.c.user_id,
            remember_tokens.c.token_hash,
        ).where(remember_tokens.c.id == int(selector))
        async with self._engine().connect() as conn:
            row = (await conn.execute(stmt)).first()
        if row is None:
            return None
        # Constant-time compare on the hash — timing must not leak matches.
        if not hmac.compare_digest(str(row.token_hash), _hash_validator(validator)):
            return None
        # Rotate in one transaction: the old pair dies exactly when its
        # replacement is born. last_used_at is stamped on the fresh row —
        # the rotation IS the "use" (spec §4.4 auth path).
        fresh_validator = secrets.token_urlsafe(48)
        now = int(time.time())
        async with self._engine().begin() as conn:
            await conn.execute(delete(remember_tokens).where(remember_tokens.c.id == row.id))
            result = await conn.execute(
                insert(remember_tokens).values(
                    user_id=row.user_id,
                    token_hash=_hash_validator(fresh_validator),
                    created_at=now,
                    last_used_at=now,
                )
            )
            fresh_id = result.inserted_primary_key[0]
        return row.user_id, f"{fresh_id}|{fresh_validator}"

    async def revoke(self, cookie: str) -> None:
        """Hash-verified delete — a wrong validator revokes nothing.

        The hash rides in the WHERE clause: one statement, atomic, and the
        row only dies when the caller actually holds the validator.
        """
        await self._ensure_table()
        selector, sep, validator = cookie.partition("|")
        if not sep or not selector.isdigit() or not validator:
            return
        async with self._engine().begin() as conn:
            await conn.execute(
                delete(remember_tokens).where(
                    remember_tokens.c.id == int(selector),
                    remember_tokens.c.token_hash == _hash_validator(validator),
                )
            )

    async def revoke_all_for_user(self, user_id: Any) -> int:
        await self._ensure_table()
        async with self._engine().begin() as conn:
            result = await conn.execute(
                delete(remember_tokens).where(remember_tokens.c.user_id == user_id)
            )
            return int(result.rowcount or 0)


_default_store: RememberTokenStore | None = None


def remember_store() -> RememberTokenStore:
    """Process singleton (lazy table create on first use)."""
    global _default_store
    if _default_store is None:
        _default_store = RememberTokenStore()
    return _default_store


def reset_remember_store() -> None:
    """Drop the singleton — tests and config reloads."""
    global _default_store
    _default_store = None
