"""Redis-backed session store — one namespaced key per session."""

from __future__ import annotations

import json
import time
from typing import Any

from fastplace.http.session.base import StoredSession, session_lifetime


class RedisSessionStore:
    """Redis driver: native TTL handles expiry; GC is server-side.

    The envelope carries ``last_activity`` inside the JSON payload so reads
    never derive it from TTL arithmetic (deterministic, immune to config
    drift between write and read).
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        url: str | None = None,
        prefix: str | None = None,
    ) -> None:
        self._client = client
        self._url = url
        self._prefix = prefix if prefix is not None else "fastplace:session:"

    @property
    def client(self) -> Any:
        """Lazy redis.asyncio client — the queue-extra pattern from cache.py."""
        if self._client is None:
            from redis import asyncio as aioredis

            from fastplace.config import config

            self._client = aioredis.from_url(
                self._url or config("REDIS_URL", default="redis://localhost:6379/0")
            )
        return self._client

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}{session_id}"

    async def read(self, session_id: str) -> StoredSession | None:
        raw = await self.client.get(self._key(session_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        envelope: dict[str, Any] = json.loads(raw)
        return StoredSession(
            payload=dict(envelope.get("payload") or {}),
            last_activity=int(envelope.get("last_activity") or 0),
        )

    async def write(
        self,
        session_id: str,
        payload: dict[str, Any],
        *,
        user_id: int | None = None,
    ) -> None:
        envelope = {
            "payload": payload,
            "last_activity": int(time.time()),
            "user_id": user_id,
        }
        await self.client.set(self._key(session_id), json.dumps(envelope), ex=session_lifetime())

    async def destroy(self, session_id: str) -> None:
        await self.client.delete(self._key(session_id))

    async def destroy_for_user(self, user_id: Any, *, except_session_id: str | None = None) -> int:
        """SCAN the namespace, delete every envelope attributed to ``user_id``."""
        removed = 0
        async for key in self.client.scan_iter(match=f"{self._prefix}*"):
            session_id = (
                key[len(self._prefix) :]
                if isinstance(key, str)
                else key.decode()[len(self._prefix) :]
            )
            if session_id == except_session_id:
                continue
            raw = await self.client.get(key)
            if raw is None:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            envelope: dict[str, Any] = json.loads(raw)
            if envelope.get("user_id") == user_id:
                await self.client.delete(key)
                removed += 1
        return removed

    async def gc(self, lifetime: int | None = None) -> int:
        # Redis expires keys server-side — nothing to sweep.
        return 0
