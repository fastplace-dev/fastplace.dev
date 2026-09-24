"""Personal access tokens — id|secret pairs, sha256-at-rest (spec §4.14).

The API client holds ``{row_id}|{random_64}``; only ``sha256(random_64)`` is
stored. Authentication is an indexed id lookup plus a timing-safe compare —
never a table-wide hash scan. Unlike remember cookies the pair never rotates
(long-lived API credential), but revocation and expiry are hard deletes /
checked on every use, and ``last_used_at`` is stamped per authentication.

Framework-owned table: ``personal_access_tokens`` lives on dedicated metadata
(the remember-tokens pattern) and is created idempotently — the app's Alembic
revision owns the same table in deployed databases (checkfirst no-ops).
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import secrets
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
    update,
)

from fastplace.orm.types import PortableDateTime, PortableJSON

UTC = datetime.UTC

_metadata = MetaData()

personal_access_tokens = Table(
    "personal_access_tokens",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("name", String(255), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("abilities", PortableJSON(), nullable=True),
    Column("last_used_at", PortableDateTime(), nullable=True),
    Column("expires_at", PortableDateTime(), nullable=True),
    Column("created_at", PortableDateTime(), nullable=True),
    Column("updated_at", PortableDateTime(), nullable=True),
    Column("deleted_at", PortableDateTime(), nullable=True),
)

# ASGI-scope markers the TokenGuard sets when a request authenticated via a
# personal access token (Request.token_can reads them).
VIA_PAT_SCOPE = "fastplace_via_pat"
PAT_ABILITIES_SCOPE = "fastplace_pat_abilities"

DEFAULT_ABILITIES = ["*"]


def _hash_token(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _now() -> datetime.datetime:
    return datetime.datetime.now(UTC)


def _ensure_aware(value: datetime.datetime) -> datetime.datetime:
    """SQLite reads timezone-aware columns back naive — assume UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class PersonalAccessTokenStore:
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
            _metadata.create_all(sync_conn, tables=[personal_access_tokens], checkfirst=True)

        async with self._engine().begin() as conn:
            await conn.run_sync(create)
        self._ensured = True

    async def issue(
        self,
        user_id: Any,
        name: str,
        *,
        abilities: list[str] | None = None,
        expires_at: datetime.datetime | None = None,
    ) -> str:
        """Insert one row; returns the plaintext ``{id}|{secret}`` exactly once."""
        await self._ensure_table()
        secret = secrets.token_urlsafe(48)  # 64 url-safe chars
        async with self._engine().begin() as conn:
            result = await conn.execute(
                insert(personal_access_tokens).values(
                    user_id=user_id,
                    name=str(name),
                    token_hash=_hash_token(secret),
                    abilities=list(abilities or DEFAULT_ABILITIES),
                    last_used_at=None,
                    expires_at=expires_at,
                    created_at=_now(),
                )
            )
            row_id = result.inserted_primary_key[0]
        return f"{row_id}|{secret}"

    async def authenticate(self, bearer: str) -> tuple[Any, list[str]] | None:
        """Validate a bearer. Returns ``(user_id, abilities)`` or None.

        Indexed id lookup (never a table-wide hash scan), constant-time
        compare on the hash, expiry checked on every use, last_used_at
        stamped per authentication (spec §4.14).
        """
        await self._ensure_table()
        selector, sep, secret = bearer.partition("|")
        if not sep or not selector.isdigit() or not secret:
            return None
        stmt = select(
            personal_access_tokens.c.user_id,
            personal_access_tokens.c.token_hash,
            personal_access_tokens.c.abilities,
            personal_access_tokens.c.expires_at,
        ).where(personal_access_tokens.c.id == int(selector))
        async with self._engine().connect() as conn:
            row = (await conn.execute(stmt)).first()
        if row is None:
            return None
        if not hmac.compare_digest(str(row.token_hash), _hash_token(secret)):
            return None
        expires_at = row.expires_at
        if expires_at is not None and _ensure_aware(expires_at) <= _now():
            return None
        await self.touch_last_used(int(selector))
        return row.user_id, list(row.abilities or DEFAULT_ABILITIES)

    async def touch_last_used(self, token_id: Any) -> None:
        await self._ensure_table()
        async with self._engine().begin() as conn:
            await conn.execute(
                update(personal_access_tokens)
                .where(personal_access_tokens.c.id == token_id)
                .values(last_used_at=_now())
            )

    async def owner_of(self, token_id: Any) -> Any | None:
        """The user id owning one token row (operator tooling; None = no row)."""
        await self._ensure_table()
        stmt = select(personal_access_tokens.c.user_id).where(
            personal_access_tokens.c.id == token_id
        )
        async with self._engine().connect() as conn:
            row = (await conn.execute(stmt)).first()
        return None if row is None else row.user_id

    async def revoke(self, token_id: Any, user_id: Any) -> bool:
        """Owner-scoped hard delete — a wrong user revokes nothing (IDOR-safe)."""
        await self._ensure_table()
        async with self._engine().begin() as conn:
            result = await conn.execute(
                delete(personal_access_tokens).where(
                    personal_access_tokens.c.id == token_id,
                    personal_access_tokens.c.user_id == user_id,
                )
            )
            return bool(result.rowcount)

    async def revoke_all_for_user(self, user_id: Any) -> int:
        await self._ensure_table()
        async with self._engine().begin() as conn:
            result = await conn.execute(
                delete(personal_access_tokens).where(personal_access_tokens.c.user_id == user_id)
            )
            return int(result.rowcount or 0)

    async def revoke_all(self) -> int:
        """Every token, every owner — operator-side incident response.

        Reachable only from trusted console context (token:revoke --all);
        HTTP paths use the owner-scoped :meth:`revoke` and per-user
        :meth:`revoke_all_for_user`.
        """
        await self._ensure_table()
        async with self._engine().begin() as conn:
            result = await conn.execute(delete(personal_access_tokens))
            return int(result.rowcount or 0)

    async def prune_expired(self) -> int:
        await self._ensure_table()
        async with self._engine().begin() as conn:
            result = await conn.execute(
                delete(personal_access_tokens).where(
                    personal_access_tokens.c.expires_at.is_not(None),
                    personal_access_tokens.c.expires_at < _now(),
                )
            )
            return int(result.rowcount or 0)

    async def _row_for(self, bearer: str) -> Any:
        """Test-support SELECT of the raw row behind a plaintext token."""
        selector = int(bearer.partition("|")[0])
        stmt = select(personal_access_tokens).where(personal_access_tokens.c.id == selector)
        async with self._engine().connect() as conn:
            return (await conn.execute(stmt)).first()


_default_store: PersonalAccessTokenStore | None = None


def pat_store() -> PersonalAccessTokenStore:
    """Process singleton (lazy table create on first use)."""
    global _default_store
    if _default_store is None:
        _default_store = PersonalAccessTokenStore()
    return _default_store


def reset_pat_store() -> None:
    """Drop the singleton — tests and config reloads."""
    global _default_store
    _default_store = None


async def create_token(
    user_id: Any,
    name: str,
    *,
    abilities: list[str] | None = None,
    expires_at: datetime.datetime | None = None,
) -> str:
    """Spec §4.14 entry point — mint a PAT; plaintext returned exactly once."""
    return await pat_store().issue(user_id, name, abilities=abilities, expires_at=expires_at)
