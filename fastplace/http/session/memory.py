"""In-memory session store — unit tests and local dev only."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastplace.http.session.base import StoredSession, session_lifetime


class MemorySessionStore:
    """Dict-backed store with lazy expiry — every op is O(1).

    The clock is injectable so expiry tests are deterministic. Not
    multi-worker safe; production apps use the database or redis driver.
    """

    def __init__(
        self,
        *,
        lifetime: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._lifetime = lifetime
        self._clock = clock
        # session_id -> (payload, last_activity, expires_at, user_id)
        self._rows: dict[str, tuple[dict[str, Any], int, float, Any]] = {}

    def _ttl(self) -> int:
        return self._lifetime if self._lifetime is not None else session_lifetime()

    async def read(self, session_id: str) -> StoredSession | None:
        row = self._rows.get(session_id)
        if row is None:
            return None
        payload, last_activity, expires_at, _user_id = row
        if self._clock() >= expires_at:
            del self._rows[session_id]  # lazy sweep
            return None
        return StoredSession(payload=dict(payload), last_activity=last_activity)

    async def write(
        self,
        session_id: str,
        payload: dict[str, Any],
        *,
        user_id: int | None = None,
    ) -> None:
        # Attribute the row to its user when the caller didn't pass one —
        # the payload key is the SessionGuard's SESSION_KEY ("user_id").
        if user_id is None:
            user_id = payload.get("user_id")
        now = int(self._clock())
        self._rows[session_id] = (dict(payload), now, now + self._ttl(), user_id)

    async def destroy(self, session_id: str) -> None:
        self._rows.pop(session_id, None)

    async def destroy_for_user(self, user_id: Any, *, except_session_id: str | None = None) -> int:
        doomed = [
            sid
            for sid, (_, _, _, row_user_id) in self._rows.items()
            if row_user_id == user_id and sid != except_session_id
        ]
        for sid in doomed:
            del self._rows[sid]
        return len(doomed)

    async def gc(self, lifetime: int | None = None) -> int:
        # The passed lifetime is advisory: rows already carry their own
        # write-time deadline, so the sweep is simply "past deadline".
        now = self._clock()
        stale = [sid for sid, (_, _, expires_at, _) in self._rows.items() if now >= expires_at]
        for sid in stale:
            del self._rows[sid]
        return len(stale)
