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
    ) -> None:
        """Persist the full payload (upsert); ``user_id`` feeds revocation."""
        ...

    async def destroy(self, session_id: str) -> None:
        """Delete one session row — logout and regeneration use this."""
        ...

    async def gc(self, lifetime: int | None = None) -> int:
        """Sweep sessions idle beyond ``lifetime``; returns rows removed."""
        ...
