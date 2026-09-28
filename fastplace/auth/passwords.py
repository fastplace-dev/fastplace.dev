"""Password-reset tokens — hashed, one live token per email (spec §4.10).

The emailed link carries the raw token plus the account email; only its PHC
hash (``Hash.make``/``Hash.check`` scrypt) is stored, keyed BY EMAIL. Issuing
a new token deletes any prior one in the same transaction — at most one live
token per address, so a fresh request always invalidates the last link.

Framework-owned table: ``password_reset_tokens`` lives on dedicated metadata
(the remember_tokens pattern), is created idempotently by this store, and
never appears in app Alembic revisions. Expiry is DERIVED (``created_at`` +
``AUTH_PASSWORD_EXPIRE`` minutes) — no TTL column to keep honest.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from sqlalchemy import Column, Integer, MetaData, String, Table, select

from fastplace.auth.hashing import Hash
from fastplace.config import config

# Framework-owned metadata — the password_reset_tokens table is
# infrastructure, never an app Alembic revision (the remember_tokens pattern).
_metadata = MetaData()

password_reset_tokens = Table(
    "password_reset_tokens",
    _metadata,
    Column("email", String(255), primary_key=True),
    Column("token_hash", String(255), nullable=False),
    Column("created_at", Integer, nullable=False),
)


def expire_seconds() -> int:
    """Token lifetime in seconds (AUTH_PASSWORD_EXPIRE minutes)."""
    return int(config("AUTH_PASSWORD_EXPIRE", default=60) or 60) * 60


def throttle_seconds() -> int:
    """Seconds between reset-link emails for one address."""
    return int(config("AUTH_RESET_THROTTLE", default=60) or 60)


_dummy: str | None = None


def dummy_digest() -> str:
    """A lazily-built scrypt digest for equal-work on unknown emails."""
    global _dummy
    if _dummy is None:
        _dummy = Hash.make("fastplace-timing-equalization-digest")
    return _dummy


class PasswordResetTokenStore:
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
            _metadata.create_all(sync_conn, tables=[password_reset_tokens], checkfirst=True)

        async with self._engine().begin() as conn:
            await conn.run_sync(create)
        self._ensured = True

    async def issue(self, email: str) -> str:
        """Create the (single) live token for ``email``; returns the raw token."""
        await self._ensure_table()
        raw = secrets.token_urlsafe(48)
        digest = Hash.make(raw)
        now = int(time.time())
        # Delete-prior + insert in ONE transaction: one live token per email.
        async with self._engine().begin() as conn:
            await conn.execute(
                password_reset_tokens.delete().where(password_reset_tokens.c.email == email)
            )
            await conn.execute(
                password_reset_tokens.insert().values(
                    email=email, token_hash=digest, created_at=now
                )
            )
        return raw

    async def _row_for(self, email: str) -> Any:
        async with self._engine().begin() as conn:
            result = await conn.execute(
                select(password_reset_tokens).where(password_reset_tokens.c.email == email)
            )
            return result.fetchone()

    def _live(self, row: Any) -> bool:
        return row is not None and row._mapping["created_at"] + expire_seconds() >= int(time.time())

    async def peek(self, email: str, token: str) -> bool:
        """Non-consuming validity check — validation failures never burn a token."""
        await self._ensure_table()
        row = await self._row_for(email)
        if not self._live(row):
            # Equal work for unknown email / expired token: a miss pays the
            # same scrypt cost a hit does (timing parity, spec §6).
            Hash.check(token, dummy_digest())
            return False
        return bool(Hash.check(token, row._mapping["token_hash"]))

    async def consume(self, email: str, token: str) -> bool:
        """Validate, then burn the verified row (atomic delete-by-hash)."""
        await self._ensure_table()
        row = await self._row_for(email)
        if not self._live(row) or not Hash.check(token, row._mapping["token_hash"]):
            # Equal work when nothing matched (timing parity).
            Hash.check(token, dummy_digest())
            return False
        async with self._engine().begin() as conn:
            result = await conn.execute(
                password_reset_tokens.delete().where(
                    password_reset_tokens.c.email == email,
                    password_reset_tokens.c.token_hash == row._mapping["token_hash"],
                )
            )
        return bool(result.rowcount)

    async def purge_expired(self, window_seconds: int | None = None) -> int:
        """Delete tokens older than the expiry window; returns the rowcount."""
        await self._ensure_table()
        window = expire_seconds() if window_seconds is None else window_seconds
        async with self._engine().begin() as conn:
            result = await conn.execute(
                password_reset_tokens.delete().where(
                    password_reset_tokens.c.created_at < int(time.time()) - window
                )
            )
        return int(result.rowcount)


_default_store: PasswordResetTokenStore | None = None


def token_store() -> PasswordResetTokenStore:
    """Process singleton (lazy table create on first use)."""
    global _default_store
    if _default_store is None:
        _default_store = PasswordResetTokenStore()
    return _default_store


def reset_token_store() -> None:
    """Drop the singleton — tests and config reloads."""
    global _default_store
    _default_store = None
