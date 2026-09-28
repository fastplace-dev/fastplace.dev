"""SessionStore contract — the driver surface every backend implements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from fastplace.config import config


@dataclass
class StoredSession:
    """What a store hands back for one live session ID."""

    payload: dict[str, Any] = field(default_factory=dict)
    last_activity: int = 0


def session_lifetime() -> int:
    """The sliding inactivity window, in seconds (config SESSION_LIFETIME)."""
    return int(config("SESSION_LIFETIME", default=7200))


@runtime_checkable
class SessionStore(Protocol):
    """Driver contract (spec §4.1) — async throughout, app-code never calls it."""

    async def read(self, session_id: str) -> StoredSession | None:
        """Fetch one live session; expired/missing IDs read as None."""
        ...

    async def write(
        self,
        session_id: str,
        payload: dict[str, Any],
        *,
        user_id: int | None = None,
    ) -> str | None:
        """Persist the full payload (upsert); ``user_id`` feeds revocation.

        Statelessness hook: a store that keeps the payload inside the cookie
        itself (``cookie`` driver) returns the encrypted value the middleware
        must set as the cookie — server-side stores return ``None`` and keep
        the minted opaque ID.
        """
        ...

    async def destroy(self, session_id: str) -> None:
        """Delete one session row — logout and regeneration use this."""
        ...

    async def destroy_for_user(self, user_id: Any, *, except_session_id: str | None = None) -> int:
        """Remove every session attributed to ``user_id`` (logout-others)."""
        ...

    async def gc(self, lifetime: int | None = None) -> int:
        """Sweep sessions idle beyond ``lifetime``; returns rows removed."""
        ...
