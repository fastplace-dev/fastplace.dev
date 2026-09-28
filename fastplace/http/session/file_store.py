"""File-backed session store — one JSON file per session under storage.

The zero-infrastructure durable driver: sessions survive restarts without a
database or redis, one ``<session_id>.json`` file per live session under
``storage/framework/sessions``. Single-node only (a multi-worker fleet must
share the filesystem at best) — production defaults stay database/redis.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from fastplace.http.session.base import StoredSession, session_lifetime

#: Session IDs are the middleware's ``secrets.token_hex(32)`` — lowercase hex.
#: A cookie value is attacker-controlled; anything outside this shape must
#: never reach the filesystem (path traversal, odd encodings).
_SAFE_ID = re.compile(r"^[a-f0-9]{32,128}$")

DEFAULT_ROOT = Path("storage") / "framework" / "sessions"


class FileSessionRow(NamedTuple):
    """What ``sessions_for_user`` lists — mirrors the database store's rows."""

    id: str
    last_activity: int
    ip_address: str | None
    user_agent: str | None


class FileSessionStore:
    """Filesystem store with lazy expiry and atomic (tmp + replace) writes."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        lifetime: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        resolved = root if root is not None else DEFAULT_ROOT
        self._root = Path(resolved)
        self._lifetime = lifetime
        self._clock = clock
        self._root.mkdir(parents=True, exist_ok=True)

    def _ttl(self) -> int:
        return self._lifetime if self._lifetime is not None else session_lifetime()

    def _path(self, session_id: str) -> Path | None:
        return self._root / f"{session_id}.json" if _SAFE_ID.match(session_id) else None

    def _read_file(self, session_id: str) -> dict[str, Any] | None:
        path = self._path(session_id)
        if path is None:
            return None
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    async def read(self, session_id: str) -> StoredSession | None:
        record = await asyncio.to_thread(self._read_file, session_id)
        if record is None:
            return None
        last_activity = int(record.get("last_activity", 0))
        # Lazy expiry: a file past the window reads as missing even before GC.
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
    ) -> None:
        if user_id is None:
            user_id = payload.get("user_id")
        record = {
            "payload": payload,
            "last_activity": int(self._clock()),
            "user_id": user_id,
        }
        path = self._path(session_id)
        if path is None:
            raise ValueError(
                f"invalid session id {session_id!r} — expected secrets.token_hex output"
            )
        await asyncio.to_thread(self._write_sync, path, record)

    def _write_sync(self, path: Path, record: dict[str, Any]) -> None:
        # Unique scratch file per write: a deterministic name lets a second
        # writer (another serve worker sharing the directory) truncate this
        # writer's in-flight tmp inode — the loser's replace would publish
        # torn JSON or die on FileNotFoundError mid-response. Unique names
        # degrade the race to last-writer-wins, which is correct for sessions.
        tmp = path.with_name(f".{path.stem}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
        try:
            tmp.write_text(json.dumps(record))
            os.replace(tmp, path)  # atomic on POSIX — a reader never sees a torn file
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    async def destroy(self, session_id: str) -> None:
        path = self._path(session_id)
        if path is not None:
            path.unlink(missing_ok=True)

    async def destroy_for_user(self, user_id: Any, *, except_session_id: str | None = None) -> int:
        # Full-directory scans can dwarf a request budget once sessions
        # accumulate — keep them off the event loop (gc rides 2% of traffic).
        return await asyncio.to_thread(self._destroy_for_user_sync, user_id, except_session_id)

    def _destroy_for_user_sync(self, user_id: Any, except_session_id: str | None) -> int:
        doomed = [
            path
            for path in self._root.glob("*.json")
            if path.stem != except_session_id
            and (record := self._safe_load(path)) is not None
            and record.get("user_id") == user_id
        ]
        for path in doomed:
            path.unlink(missing_ok=True)
        return len(doomed)

    async def sessions_for_user(self, user_id: Any) -> list[FileSessionRow]:
        """The user's ACTIVE sessions, newest activity first (auth:sessions)."""
        threshold = self._clock() - self._ttl()
        records = await asyncio.to_thread(self._sessions_for_user_sync, user_id, threshold)
        rows = [
            FileSessionRow(
                id=path.stem,
                last_activity=int(record.get("last_activity", 0)),
                ip_address=None,
                user_agent=None,
            )
            for path, record in records
        ]
        rows.sort(key=lambda row: row.last_activity, reverse=True)
        return rows

    def _sessions_for_user_sync(
        self, user_id: Any, threshold: float
    ) -> list[tuple[Path, dict[str, Any]]]:
        matches: list[tuple[Path, dict[str, Any]]] = []
        for path in self._root.glob("*.json"):
            record = self._safe_load(path)
            if record is None:
                continue
            if int(record.get("last_activity", 0)) < threshold or record.get("user_id") != user_id:
                continue
            matches.append((path, record))
        return matches

    async def gc(self, lifetime: int | None = None) -> int:
        window = lifetime if lifetime is not None else self._ttl()
        return await asyncio.to_thread(self._gc_sync, self._clock() - window)

    def _gc_sync(self, threshold: float) -> int:
        stale = [
            path
            for path in self._root.glob("*.json")
            if (record := self._safe_load(path)) is not None
            and int(record.get("last_activity", 0)) < threshold
        ]
        for path in stale:
            path.unlink(missing_ok=True)
        return len(stale)

    def _safe_load(self, path: Path) -> dict[str, Any] | None:
        try:
            record = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return record if isinstance(record, dict) else None
