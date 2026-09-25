"""cache:gc — sweep expired rows from the database cache (roadmap A5)."""

from __future__ import annotations

import asyncio
import os
import re

import pytest
from _isolation import isolate_project_state  # noqa: F401
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them."""
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


def _out(result) -> str:
    return ANSI_RE.sub("", result.output)


@pytest.fixture
def database_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("CACHE_DRIVER", "database")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/x.db")
    from fastplace.cache import cache, reset_cache

    reset_cache()
    store = cache()

    async def _seed() -> None:
        await store.put("dead", "x", ttl=60)
        await store.put("live", "y", ttl=3600)
        from fastplace.cache import _cache_table
        from fastplace.db import db

        async with db.manager.engine("default").begin() as conn:
            await conn.execute(
                _cache_table.update()
                .where(_cache_table.c.key == store._key("dead"))
                .values(expires_at=1)
            )

    asyncio.run(_seed())
    return store


def test_purges_expired_rows_and_reports_count(database_cache):
    result = runner.invoke(cli_app, ["cache:gc"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "Purged 1 expired row(s)." in out


def test_dry_run_deletes_nothing(database_cache):
    result = runner.invoke(cli_app, ["cache:gc", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Would purge 1 expired row(s)." in _out(result)

    async def _count() -> int:
        from fastplace.cache import _cache_table
        from fastplace.db import db

        async with db.manager.engine("default").connect() as conn:
            rows = await conn.execute(_cache_table.select())
            return len(rows.fetchall())

    assert asyncio.run(_count()) == 2


def test_live_rows_survive(database_cache):
    runner.invoke(cli_app, ["cache:gc"])
    assert asyncio.run(database_cache.get("live")) == "y"


def test_dry_run_on_virgin_database_writes_nothing(tmp_path, monkeypatch):
    """--dry-run must neither crash nor write on a first-run project.

    A first-run project has no cache table; the dry-run count must report
    zero without creating it — creating schema is a write, exactly what
    --dry-run promises not to do. The real path's purge_expired() still
    self-heals via its own _ensure_table(). No seeding here: the
    database_cache fixture's store.put() is what creates the table and
    would hide this gap.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("CACHE_DRIVER", "database")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/x.db")
    result = runner.invoke(cli_app, ["cache:gc", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Would purge 0 expired row(s)." in _out(result)

    from sqlalchemy import inspect as sa_inspect

    from fastplace.cache import _cache_table
    from fastplace.db import db

    def _cache_table_exists() -> bool:
        async def _check() -> bool:
            engine = db.manager.engine("default")
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync_conn: sa_inspect(sync_conn).has_table(_cache_table.name)
                )

        return asyncio.run(_check())

    assert not _cache_table_exists()


def test_memory_driver_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("CACHE_DRIVER", "memory")
    result = runner.invoke(cli_app, ["cache:gc"])
    assert result.exit_code == 0, result.output
    assert "nothing to sweep" in _out(result)
