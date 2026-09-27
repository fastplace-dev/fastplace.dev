"""Passkey credential store — framework-owned ``webauthn_credentials`` table.

The remember_tokens pattern: the table lives on dedicated metadata (framework
infrastructure, never an app Alembic revision), is created idempotently on
first use, and every query goes through SQLAlchemy Core so SQLite, PostgreSQL
and MySQL behave identically. Rows are returned as engine-agnostic records via
``mappings()`` — no driver-specific row shapes leak to callers.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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

from fastplace.auth.webauthn import VerifiedMaterial

# Framework-owned metadata — passkey credentials are auth infrastructure,
# never an app Alembic revision (the remember_tokens pattern).
_metadata = MetaData()

webauthn_credentials = Table(
    "webauthn_credentials",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("name", String(255), nullable=False, default=""),
    Column("credential_id", String(2048), nullable=False, unique=True),
    Column("public_key", Text, nullable=False),
    Column("sign_count", BigInteger, nullable=False, default=0),
    Column("transports", Text, nullable=True),
    Column("backup_eligible", Boolean, nullable=False, default=False),
    Column("backup_state", Boolean, nullable=False, default=False),
    Column("aaguid", String(36), nullable=True),
    Column("last_used_at", Integer, nullable=True),
    Column("created_at", Integer, nullable=False),
)


class PasskeyStore:
    """SQLAlchemy Core credential store — portable across SQLite/PG/MySQL."""

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
            _metadata.create_all(sync_conn, tables=[webauthn_credentials], checkfirst=True)

        async with self._engine().begin() as conn:
            await conn.run_sync(create)
        self._ensured = True

    @staticmethod
    def _decode_transports(raw: Any) -> list[str]:
        """The transports column is a JSON list; corrupt values degrade to []."""
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [str(t) for t in parsed]

    async def create(self, user_id: int, name: str, material: VerifiedMaterial) -> int:
        """Insert one credential; returns the new row id."""
        await self._ensure_table()
        async with self._engine().begin() as conn:
            result = await conn.execute(
                insert(webauthn_credentials).values(
                    user_id=user_id,
                    name=name,
                    credential_id=material.credential_id,
                    public_key=material.public_key,
                    sign_count=int(material.sign_count),
                    transports=json.dumps(material.transports),
                    backup_eligible=bool(material.backup_eligible),
                    backup_state=bool(material.backup_state),
                    aaguid=material.aaguid,
                    last_used_at=None,
                    created_at=int(time.time()),
                )
            )
            row_id = result.inserted_primary_key[0]
        return int(row_id)

    async def _fetch(self, statement: Any) -> SimpleNamespace | None:
        """One row as an attribute-access record, driver-agnostic."""
        async with self._engine().connect() as conn:
            mapping = (await conn.execute(statement)).mappings().first()
        return SimpleNamespace(**mapping) if mapping is not None else None

    async def get_by_credential_id(self, credential_id: str) -> SimpleNamespace | None:
        """Look up by the exact browser-shaped (unpadded base64url) id."""
        await self._ensure_table()
        statement = select(webauthn_credentials).where(
            webauthn_credentials.c.credential_id == credential_id
        )
        return await self._fetch(statement)

    async def rows_for(self, user_id: int) -> list[SimpleNamespace]:
        """All of one user's rows (confirm allowList, registration excludes)."""
        await self._ensure_table()
        statement = (
            select(webauthn_credentials)
            .where(webauthn_credentials.c.user_id == user_id)
            .order_by(webauthn_credentials.c.id)
        )
        async with self._engine().connect() as conn:
            rows = (await conn.execute(statement)).mappings().all()
        return [SimpleNamespace(**mapping) for mapping in rows]

    async def list_for(self, user_id: int) -> list[dict[str, Any]]:
        """Serialized passkeys matching the frontend ``Passkey`` type exactly."""
        listed: list[dict[str, Any]] = []
        for row in await self.rows_for(user_id):
            listed.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "authenticator": humanize_authenticator(
                        row.aaguid, self._decode_transports(row.transports)
                    ),
                    "created_at_diff": humanize_diff(row.created_at),
                    "last_used_at_diff": humanize_diff(row.last_used_at),
                }
            )
        return listed

    async def delete(self, user_id: int, id: int) -> bool:
        """Owner-filtered delete — False when the row is not this user's."""
        await self._ensure_table()
        async with self._engine().begin() as conn:
            result = await conn.execute(
                delete(webauthn_credentials).where(
                    webauthn_credentials.c.id == id,
                    webauthn_credentials.c.user_id == user_id,
                )
            )
        return bool(result.rowcount)

    async def touch(self, credential_id: str, sign_count: int, backup_state: bool) -> None:
        """Stamp the verified counter, backup state, and last_used_at."""
        await self._ensure_table()
        async with self._engine().begin() as conn:
            await conn.execute(
                update(webauthn_credentials)
                .where(webauthn_credentials.c.credential_id == credential_id)
                .values(
                    sign_count=int(sign_count),
                    backup_state=bool(backup_state),
                    last_used_at=int(time.time()),
                )
            )


def humanize_diff(epoch: int | None) -> str:
    """Epoch seconds to a coarse relative string ("5 minutes ago")."""
    if epoch is None:
        return "never"
    delta = max(0, int(time.time()) - int(epoch))
    if delta < 60:
        return "just now"
    minutes = delta // 60
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = delta // 3600
    if hours < 24:
        return f"{hours} hours ago"
    days = delta // 86400
    if days < 7:
        return f"{days} days ago"
    if days < 30:
        return f"{days // 7} weeks ago"
    if days < 365:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"


def humanize_authenticator(aaguid: str | None, transports: list[str] | None) -> str | None:
    """Rough authenticator family from its transports (aaguid reserved)."""
    if transports and "internal" in transports:
        return "This device"
    if transports and "hybrid" in transports:
        return "Phone as a security key"
    if transports and ("usb" in transports or "nfc" in transports):
        return "Security key"
    if transports:
        return "Portable authenticator"
    return None


_default_store: PasskeyStore | None = None


def passkey_store() -> PasskeyStore:
    """Process singleton (lazy table create on first use)."""
    global _default_store
    if _default_store is None:
        _default_store = PasskeyStore()
    return _default_store


def reset_passkey_store() -> None:
    """Drop the singleton — tests and config reloads."""
    global _default_store
    _default_store = None
