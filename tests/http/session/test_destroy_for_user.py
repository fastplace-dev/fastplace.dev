"""T2 — per-user session revocation on every driver (spec §4.6)."""

from __future__ import annotations

import fnmatch

import httpx
import pytest

from fastplace.http.session.database import DatabaseSessionStore
from fastplace.http.session.memory import MemorySessionStore
from fastplace.http.session.middleware import ServerSessionMiddleware
from fastplace.http.session.redis_store import RedisSessionStore


class FakeScanRedis:
    """set/get/delete/scan_iter — the surface destroy_for_user touches."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.deleted: list[str] = []

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.store.pop(key, None)

    async def scan_iter(self, match: str = "*"):
        for key in list(self.store):
            if fnmatch.fnmatch(key, match):
                yield key


class TestMemoryStore:
    async def test_destroy_for_user_removes_only_that_users_sessions(self):
        store = MemorySessionStore()
        await store.write("s-a", {"user_id": 7}, user_id=7)
        await store.write("s-b", {"user_id": 7}, user_id=7)
        await store.write("s-c", {"user_id": 9, "cart": [1]}, user_id=9)

        removed = await store.destroy_for_user(7)

        assert removed == 2
        assert await store.read("s-a") is None
        assert await store.read("s-b") is None
        assert await store.read("s-c") is not None  # other user untouched

    async def test_destroy_for_user_spares_the_excepted_session(self):
        store = MemorySessionStore()
        await store.write("s-a", {"user_id": 7}, user_id=7)
        await store.write("s-b", {"user_id": 7}, user_id=7)

        removed = await store.destroy_for_user(7, except_session_id="s-b")

        assert removed == 1
        assert await store.read("s-a") is None
        assert await store.read("s-b") is not None

    async def test_write_derives_the_user_from_the_payload(self):
        # EC2 safety net: a caller that omits the kwarg still attributes
        # the row via the payload's user_id key.
        store = MemorySessionStore()
        await store.write("s-a", {"user_id": 7})

        assert await store.destroy_for_user(7) == 1


class TestDatabaseStore:
    @pytest.fixture(autouse=True)
    def _fresh_db(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        from fastplace.db import reset_db

        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/sessions.db")
        monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
        reset_db()
        yield
        reset_db()

    async def test_destroy_for_user_removes_only_that_users_sessions(self):
        store = DatabaseSessionStore()
        await store.write("s-a", {"user_id": 7}, user_id=7)
        await store.write("s-b", {"user_id": 7}, user_id=7)
        await store.write("s-c", {"user_id": 9}, user_id=9)

        removed = await store.destroy_for_user(7)

        assert removed == 2
        assert await store.read("s-a") is None
        assert await store.read("s-c") is not None

    async def test_destroy_for_user_spares_the_excepted_session(self):
        store = DatabaseSessionStore()
        await store.write("s-a", {"user_id": 7}, user_id=7)
        await store.write("s-b", {"user_id": 7}, user_id=7)

        removed = await store.destroy_for_user(7, except_session_id="s-b")

        assert removed == 1
        assert await store.read("s-a") is None
        assert await store.read("s-b") is not None


class TestRedisStore:
    async def test_destroy_for_user_deletes_exactly_the_envelope_matching_keys(self):
        fake = FakeScanRedis()
        store = RedisSessionStore(client=fake)
        await store.write("s-a", {"user_id": 7}, user_id=7)
        await store.write("s-b", {"user_id": 7}, user_id=7)
        await store.write("s-c", {"user_id": 9}, user_id=9)

        removed = await store.destroy_for_user(7)

        assert removed == 2
        assert fake.deleted == ["fastplace:session:s-a", "fastplace:session:s-b"]
        assert await store.read("s-c") is not None

    async def test_destroy_for_user_spares_the_excepted_session(self):
        fake = FakeScanRedis()
        store = RedisSessionStore(client=fake)
        await store.write("s-a", {"user_id": 7}, user_id=7)
        await store.write("s-b", {"user_id": 7}, user_id=7)

        removed = await store.destroy_for_user(7, except_session_id="s-b")

        assert removed == 1
        assert fake.deleted == ["fastplace:session:s-a"]
        assert await store.read("s-b") is not None


class SpyStore(MemorySessionStore):
    """Memory store that records the write() kwargs the middleware passed."""

    def __init__(self) -> None:
        super().__init__()
        self.write_kwargs: list[dict] = []

    async def write(self, session_id, payload, *, user_id=None) -> None:
        self.write_kwargs.append({"user_id": user_id})
        await super().write(session_id, payload, user_id=user_id)


class TestMiddlewareAttribution:
    async def test_scope_exposes_the_store_and_persist_derives_the_user_id(self):
        store = SpyStore()
        seen: list[object] = []

        async def app(scope, receive, send):
            seen.append(scope.get("fastplace_session_store"))
            scope["session"]["user_id"] = 7  # a login-shaped payload write
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = ServerSessionMiddleware(app, store=store)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            response = await client.get("/login")

        assert response.status_code == 200
        # Guards (logout_other_devices) reach the live store via the scope.
        assert seen == [store]
        # EC2: _persist derived the row's user from the payload key.
        assert store.write_kwargs == [{"user_id": 7}]
