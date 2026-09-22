"""ServerSession + ServerSessionMiddleware — opaque-ID server-side sessions."""

from __future__ import annotations

import logging
import random
import secrets
import time
from http.cookies import SimpleCookie
from typing import Any

from starlette.datastructures import Headers

from fastplace.http.session.base import SessionStore

logger = logging.getLogger("fastplace.session")

_GC_LOTTERY = 0.02  # 2% of requests sweep stale sessions


class ServerSession(dict):
    """A dict the whole stack already speaks, with persistence semantics.

    Lives at ``scope["session"]`` — Starlette's ``request.session`` returns
    it verbatim, so guards/CSRF/render need zero changes. Dirty tracking is
    a snapshot compare: no mutation interception required.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._id: str | None = None
        self._previous_id: str | None = None
        self._loaded: dict[str, Any] = {}
        self._loaded_last_activity: int = 0
        self._invalidate = False

    @classmethod
    def new(cls) -> ServerSession:
        return cls()

    @classmethod
    def hydrate(cls, session_id: str, stored: Any) -> ServerSession:
        session = cls(stored.payload)
        session._id = session_id
        session._loaded = dict(stored.payload)
        session._loaded_last_activity = stored.last_activity
        return session

    # -- state -------------------------------------------------------------
    @property
    def session_id(self) -> str | None:
        return self._id

    @property
    def is_dirty(self) -> bool:
        """Fresh, rotated, or mutated since the last load/write."""
        return self._id is None or self._previous_id is not None or dict(self) != self._loaded

    def needs_touch(self, lifetime: int, now: int) -> bool:
        """Sliding window: refresh after half the lifetime of inactivity."""
        return (
            self._loaded_last_activity > 0 and (now - self._loaded_last_activity) >= lifetime // 2
        )

    def mark_persisted(
        self, session_id: str, payload: dict[str, Any], *, last_activity: int | None = None
    ) -> None:
        self._id = session_id
        self._previous_id = None
        self._loaded = dict(self)  # the live payload is the clean baseline
        self._loaded_last_activity = (
            last_activity if last_activity is not None else int(time.time())
        )

    # -- lifecycle ---------------------------------------------------------
    def regenerate(self) -> None:
        """Session-fixation defense: the next persist mints a fresh ID and
        destroys the row behind ``_previous_id``."""
        self._previous_id = self._id
        self._id = None  # is_dirty -> True; persist mints the new ID

    def invalidate(self) -> None:
        """Logout semantics: clear payload AND flag the row for destruction."""
        self.clear()
        self._invalidate = True


class ServerSessionMiddleware:
    """Pure-ASGI: load on request, persist on response start, GC in finally."""

    def __init__(
        self,
        app: Any,
        *,
        store: SessionStore,
        cookie_name: str = "fastplace_session",
        lifetime: int = 7200,
        path: str = "/",
        domain: str | None = None,
        secure: bool = False,
        gc_lottery: float = _GC_LOTTERY,
        rng: Any = random.random,
    ) -> None:
        self.app = app
        self.store = store
        self.cookie_name = cookie_name
        self.lifetime = lifetime
        self.path = path
        self.domain = domain
        self.secure = secure
        self.gc_lottery = gc_lottery
        self.rng = rng

    def _cookie_header(self, session_id: str, max_age: int) -> str:
        cookie: SimpleCookie = SimpleCookie()
        cookie[self.cookie_name] = session_id
        morsel = cookie[self.cookie_name]
        morsel["httponly"] = True
        morsel["samesite"] = "lax"
        morsel["max-age"] = max_age
        morsel["path"] = self.path
        if self.domain:
            morsel["domain"] = self.domain
        if self.secure:
            morsel["secure"] = True
        return cookie.output(header="", sep="").strip()

    def _delete_cookie_header(self) -> str:
        return self._cookie_header("", max_age=0)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        scope["session"] = await self._load(scope)
        session: ServerSession = scope["session"]

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                await self._persist(session, message)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if self.rng() < self.gc_lottery:
                try:
                    await self.store.gc()
                except Exception:  # noqa: BLE001 — GC must never kill a response
                    logger.warning("session gc failed", exc_info=True)

    async def _load(self, scope: dict) -> ServerSession:
        """Read the cookie, load the row — or start a fresh empty session."""
        headers = Headers(scope=scope)
        raw = headers.get("cookie", "")
        cookie: SimpleCookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:  # malformed cookie header -> fresh session
            cookie = SimpleCookie()
        morsel = cookie.get(self.cookie_name)
        if morsel is None or not morsel.value:
            return ServerSession.new()
        stored = await self.store.read(morsel.value)
        if stored is None:  # expired or unknown ID -> fresh session
            return ServerSession.new()
        return ServerSession.hydrate(morsel.value, stored)

    async def _persist(self, session: ServerSession, message: dict) -> None:
        now = int(time.time())
        headers = list(message.get("headers") or [])

        if session._invalidate:
            # Logout: destroy the backing row, expire the cookie.
            if session.session_id:
                await self.store.destroy(session.session_id)
            headers.append((b"set-cookie", self._delete_cookie_header().encode("latin-1")))
            message["headers"] = headers
            return

        if not session:
            # Nothing in the payload — not worth a row or a cookie mint. (A
            # logout clear goes through the invalidate path above.)
            return

        # Write on mutation, on rotation, or on the half-life touch — a
        # read-only visit inside the window costs no store round-trip.
        if not session.is_dirty and not session.needs_touch(self.lifetime, now):
            return

        session_id = session.session_id or secrets.token_hex(32)
        previous_id = session._previous_id
        await self.store.write(session_id, dict(session))
        if previous_id and previous_id != session_id:
            await self.store.destroy(previous_id)  # regenerate() cleanup
        session.mark_persisted(session_id, dict(session), last_activity=now)
        # Re-issue on every write: the browser cookie's Max-Age is absolute,
        # so a touched (sliding) session needs a fresh cookie too.
        headers.append(
            (b"set-cookie", self._cookie_header(session_id, self.lifetime).encode("latin-1"))
        )
        message["headers"] = headers
