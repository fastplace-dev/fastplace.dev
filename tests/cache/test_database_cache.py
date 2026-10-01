"""T3 — database cache driver (spec §4.6: cache.py gains a database driver).

Real sqlite file per test (the DatabaseSessionStore fixture pattern) — the
driver is Core-only and must stay portable across SQLite/PG/MySQL.
"""

from __future__ import annotations

import pytest

from fastplace.cache import DatabaseCache
from fastplace.db import reset_db


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/cache.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    yield
    reset_db()


class TestDatabaseCache:
    async def test_put_get_roundtrip(self):
        cache = DatabaseCache()
        await cache.put("k", {"a": 1}, ttl=60)
        assert await cache.get("k") == {"a": 1}

    async def test_get_missing_returns_none(self):
        assert await DatabaseCache().get("missing") is None

    async def test_put_is_an_upsert_not_a_duplicate(self):
        cache = DatabaseCache()
        await cache.put("k", 1, ttl=60)
        await cache.put("k", 2, ttl=60)
        assert await cache.get("k") == 2

    async def test_put_without_ttl_lives_forever(self):
        # Driver parity with memory/redis: put() without a ttl never expires
        # (CACHE_TTL is the remember() default, not a put() default).
        cache = DatabaseCache()
        await cache.put("durable", "v")
        assert await cache.get("durable") == "v"
        assert await cache.ttl("durable") is None

    async def test_forget_and_flush(self):
        cache = DatabaseCache()
        await cache.put("k1", 1, ttl=60)
        await cache.put("k2", 2, ttl=60)
        await cache.forget("k1")
        assert await cache.get("k1") is None
        await cache.flush()
        assert await cache.get("k2") is None

    async def test_increment_counts_and_reports_ttl(self):
        cache = DatabaseCache()
        assert await cache.increment("hits", ttl=60) == 1
        assert await cache.increment("hits", ttl=60) == 2
        remaining = await cache.ttl("hits")
        assert remaining is not None and 0 < remaining <= 60

    async def test_increment_re_arms_the_window_on_every_hit(self):
        # Sliding-decay parity with the memory/redis drivers: a hit on a
        # still-live counter pushes its deadline back out to now + ttl
        # instead of letting the first hit's window expire mid-sequence.
        import time as time_module

        from sqlalchemy import update

        from fastplace.cache import _cache_table
        from fastplace.db import db

        cache = DatabaseCache()
        await cache.increment("hits", ttl=60)

        # Shrink the row's deadline to ~10s left (still live).
        engine = db.manager.engine("default")
        async with engine.begin() as conn:
            await conn.execute(
                update(_cache_table)
                .where(_cache_table.c.key == cache._key("hits"))
                .values(expires_at=time_module.time() + 10)
            )

        assert await cache.increment("hits", ttl=60) == 2
        remaining = await cache.ttl("hits")
        assert remaining is not None and remaining > 30  # re-armed, not the dying 10

    async def test_increment_restarts_after_expiry(self):
        # An expired row reads as missing, not as a stale integer — the next
        # increment restarts at 1 (Review Focus #5, at the row level).
        from sqlalchemy import update

        from fastplace.cache import _cache_table
        from fastplace.db import db

        cache = DatabaseCache()
        await cache.increment("hits", ttl=60)

        # Force the row past its deadline (expires_at is unix seconds).
        engine = db.manager.engine("default")
        async with engine.begin() as conn:
            await conn.execute(
                update(_cache_table)
                .where(_cache_table.c.key == cache._key("hits"))
                .values(expires_at=1)
            )

        assert await cache.get("hits") is None  # expired row reads as missing
        assert await cache.increment("hits", ttl=60) == 1

    async def test_remember_caches_the_factory_result(self):
        cache = DatabaseCache()
        calls = []

        async def factory() -> int:
            calls.append(1)
            return 42

        assert await cache.remember("exp", ttl=60, factory=factory) == 42
        assert await cache.remember("exp", ttl=60, factory=factory) == 42
        assert len(calls) == 1

    async def test_remember_caches_a_none_result(self):
        # None is a legitimate cached value (negative lookups) — driver parity
        # with memory/redis: the factory must not re-run on every call.
        cache = DatabaseCache()
        calls = []

        async def factory():
            calls.append(1)
            return None

        assert await cache.remember("nil", ttl=60, factory=factory) is None
        assert await cache.remember("nil", ttl=60, factory=factory) is None
        assert len(calls) == 1

    async def test_put_of_unserializable_value_raises_a_clear_error(self):
        import datetime

        from fastplace.errors import FastplaceError

        with pytest.raises(FastplaceError, match="datetime"):
            await DatabaseCache().put("when", datetime.datetime.now(tz=datetime.UTC))

    async def test_purge_expired_removes_only_expired_rows(self):
        # The lazy sweep in _lookup only fires on touched keys — purge_expired
        # must clear the expired row and leave the live one untouched.
        from sqlalchemy import update

        from fastplace.cache import _cache_table
        from fastplace.db import db

        cache = DatabaseCache()
        await cache.put("dead", "x", ttl=60)
        await cache.put("live", "y", ttl=60)

        # Force one row into the past without sleeping (expires_at is unix seconds).
        engine = db.manager.engine("default")
        async with engine.begin() as conn:
            await conn.execute(
                update(_cache_table)
                .where(_cache_table.c.key == cache._key("dead"))
                .values(expires_at=1)
            )

        purged = await cache.purge_expired()

        assert purged == 1
        assert await cache.get("dead") is None
        assert await cache.get("live") == "y"

    async def test_purge_expired_with_injected_now(self):
        # now=wall clock + 600 is past the ttl=60 deadline but inside the
        # ttl=3600 one — the injected clock is the threshold, not time.time().
        import time

        cache = DatabaseCache()
        await cache.put("gone", "x", ttl=60)
        await cache.put("kept", "y", ttl=3600)

        purged = await cache.purge_expired(now=int(time.time()) + 600)

        assert purged == 1
        assert await cache.get("gone") is None
        assert await cache.get("kept") == "y"

    async def test_purge_expired_empty_table_returns_zero(self):
        cache = DatabaseCache()
        assert await cache.purge_expired() == 0
