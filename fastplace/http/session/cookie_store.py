"""Encrypted-cookie session store — the whole payload lives in the cookie.

Stateless by construction: nothing is persisted server-side, so the driver
scales across workers with zero infrastructure and sessions survive restarts.
The trade-offs are inherent, not configurable — a cookie session cannot be
revoked server-side (logout-others destroys nothing), and the payload must
stay small (browsers cap cookies around 4KB). APP_KEY rotation invalidates
every outstanding session cookie at once.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from fastplace.errors import ConfigurationError
from fastplace.http.session.base import StoredSession, session_lifetime

#: Distinct HKDF domain — a session blob never decrypts as 2FA material
#: (or any other ``fpaes1`` token) under the same APP_KEY.
COOKIE_HKDF_INFO = "session"

#: Browsers reject cookies past ~4096 bytes per cookie. Leave headroom for
#: the envelope (name, attributes) the middleware adds around the value.
_MAX_COOKIE_VALUE = 3800


class CookieSessionStore:
    """Session store where the cookie value IS the encrypted payload."""

    def __init__(
        self,
        *,
        lifetime: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._lifetime = lifetime
        self._clock = clock

    def _ttl(self) -> int:
        return self._lifetime if self._lifetime is not None else session_lifetime()

    async def read(self, session_id: str) -> StoredSession | None:
        """``session_id`` is the cookie blob — decrypt it, or read as missing.

        A tampered/stale/foreign blob raises inside :func:`decrypt`; a session
        the app cannot trust reads as a fresh session, never a 500.
        """
        from fastplace.auth.encryption import decrypt

        try:
            record = json.loads(decrypt(session_id, info=COOKIE_HKDF_INFO))
        except ValueError:
            return None
        if not isinstance(record, dict):
            return None
        last_activity = int(record.get("last_activity", 0))
        # Lazy expiry: the middleware's half-life touch rewrites the blob, so
        # a blob past the window reads as missing.
        if self._clock() - last_activity >= self._ttl():
            return None
        payload = record.get("payload") or {}
        return StoredSession(payload=dict(payload), last_activity=last_activity)

    async def write(
        self,
        session_id: str,
        payload: dict[str, Any],
        *,
        user_id: int | None = None,
    ) -> str:
        """Encrypt the payload and RETURN it — the middleware sets the cookie
        to this value (the ``str | None`` write contract)."""
        from fastplace.auth.encryption import encrypt

        blob = encrypt(
            json.dumps({"payload": payload, "last_activity": int(self._clock())}),
            info=COOKIE_HKDF_INFO,
        )
        if len(blob) > _MAX_COOKIE_VALUE:
            raise ConfigurationError(
                "the cookie session payload exceeds the ~4KB browser cookie limit — "
                "store large data server-side (SESSION_DRIVER=database or file)"
            )
        return blob

    async def destroy(self, session_id: str) -> None:
        return None  # stateless — cookie deletion is the middleware's job

    async def destroy_for_user(self, user_id: Any, *, except_session_id: str | None = None) -> int:
        return 0  # nothing is stored server-side — nothing to revoke

    async def gc(self, lifetime: int | None = None) -> int:
        return 0  # expiry is read-time, not a sweep
