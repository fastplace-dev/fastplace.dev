"""DatabaseSessionStore — real sqlite file, Core-only access (spec §4.1)."""

from __future__ import annotations

import time

import pytest


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from fastplace.db import reset_db

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/sessions.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    yield
    reset_db()


async def test_write_then_read_roundtrips_payload():
    from fastplace.http.session.database import DatabaseSessionStore

    store = DatabaseSessionStore()
    await store.write("sid-1", {"_token": "abc", "user_id": 7}, user_id=7)
    stored = await store.read("sid-1")
    assert stored is not None
    assert stored.payload == {"_token": "abc", "user_id": 7}
    assert stored.last_activity > 0


async def test_read_missing_returns_none():
    from fastplace.http.session.database import DatabaseSessionStore

    store = DatabaseSessionStore()
    assert await store.read("nope") is None


async def test_write_is_an_upsert_not_a_duplicate():
    from fastplace.db import db
    from fastplace.http.session.database import DatabaseSessionStore

    store = DatabaseSessionStore()
    await store.write("sid-1", {"v": 1})
    await store.write("sid-1", {"v": 2})
    rows = await db.raw("SELECT COUNT(*) AS n FROM sessions")
    assert rows[0]["n"] == 1
    stored = await store.read("sid-1")
    assert stored is not None
    assert stored.payload == {"v": 2}


async def test_destroy_removes_only_the_target_row():
    from fastplace.http.session.database import DatabaseSessionStore

    store = DatabaseSessionStore()
    await store.write("sid-1", {"k": 1})
    await store.write("sid-2", {"k": 2})
    await store.destroy("sid-1")
    assert await store.read("sid-1") is None
    assert (await store.read("sid-2")) is not None


async def test_expired_row_reads_as_missing():
    from fastplace.db import db
    from fastplace.http.session.database import DatabaseSessionStore

    store = DatabaseSessionStore()
    await store.write("sid-1", {"k": 1})
    stale = int(time.time()) - 7201  # past the default 7200s window
    await db.raw(
        "UPDATE sessions SET last_activity = :la WHERE id = :id",
        params={"la": stale, "id": "sid-1"},
    )
    assert await store.read("sid-1") is None


async def test_gc_removes_only_stale_rows():
    from fastplace.db import db
    from fastplace.http.session.database import DatabaseSessionStore

    store = DatabaseSessionStore()
    await store.write("old", {"k": 1})
    await store.write("new", {"k": 2})
    stale = int(time.time()) - 7201
    await db.raw(
        "UPDATE sessions SET last_activity = :la WHERE id = :id",
        params={"la": stale, "id": "old"},
    )
    removed = await store.gc()
    assert removed == 1
    assert await store.read("old") is None
    assert (await store.read("new")) is not None
