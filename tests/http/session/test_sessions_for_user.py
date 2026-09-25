"""sessions_for_user — read-only enumeration on both durable stores."""

from __future__ import annotations

import time

import pytest

from fastplace.http.session.base import session_lifetime
from fastplace.http.session.database import DatabaseSessionStore
from fastplace.http.session.redis_store import RedisSessionStore


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from fastplace.db import reset_db

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/sessions.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    yield
    reset_db()


class FakeRedis:
    """set/get/delete/scan_iter — the store's whole surface."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)

    async def scan_iter(self, match=None):
        import fnmatch

        for key in list(self.store):
            if match is None or fnmatch.fnmatch(key, match):
                yield key


# --- database ---------------------------------------------------------------


async def test_database_lists_only_that_users_active_sessions():
    store = DatabaseSessionStore()
    now = int(time.time())
    await store.write("sess-a", {"k": 1}, user_id=7)
    await store.write("sess-b", {"k": 2}, user_id=7)
    await store.write("sess-c", {"k": 3}, user_id=9)
    rows = await store.sessions_for_user(7)
    assert sorted(row.id for row in rows) == ["sess-a", "sess-b"]
    for row in rows:
        assert row.ip_address is None and row.user_agent is None
        assert abs(row.last_activity - now) < 5


async def test_database_excludes_expired_rows():
    store = DatabaseSessionStore()
    await store.write("fresh", {"k": 1}, user_id=7)
    # A row older than the lifetime reads as missing — the listing must agree.
    import sqlalchemy as sa

    from fastplace.http.session.database import sessions_table

    async with store._engine().begin() as conn:
        await conn.execute(
            sa.update(sessions_table)
            .where(sessions_table.c.id == "fresh")
            .values(last_activity=int(time.time()) - session_lifetime() - 10)
        )
    assert await store.sessions_for_user(7) == []


# --- redis ------------------------------------------------------------------


async def test_redis_lists_sessions_attributed_to_the_user():
    redis = FakeRedis()
    store = RedisSessionStore(client=redis)
    await store.write("sid-1", {"k": 1}, user_id=7)
    await store.write("sid-2", {"k": 2}, user_id=8)
    await store.write("sid-3", {"k": 3}, user_id=7)
    found = await store.sessions_for_user(7)
    assert sorted(session_id for session_id, _ in found) == ["sid-1", "sid-3"]
    for _, last_activity in found:
        assert last_activity > 0


async def test_redis_with_no_sessions_returns_empty():
    store = RedisSessionStore(client=FakeRedis())
    assert await store.sessions_for_user(42) == []
