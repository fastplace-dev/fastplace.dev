"""SQLite dialect specifics — serializing pool, JSON vector fallback, batch DDL.

The blueprint's dialect split (§8): this suite runs only against SQLite and
pins the behavior the other backends don't share.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from fastplace.db import db, reset_db
from fastplace.orm import Field, Model

runner = CliRunner()


@pytest.fixture(autouse=True)
def sqlite_only(monkeypatch, tmp_path):
    """Every test here rides the sqlite driver — dialect suite by design."""
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")


async def test_file_backed_sqlite_uses_the_serializing_pool(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'dialect.sqlite3'}")
    reset_db()

    pool = db.manager.engine().pool
    assert type(pool).__name__ == "AsyncAdaptedQueuePool"
    assert pool.size() == 1  # one connection — check-then-act races impossible

    await db.dispose()


async def test_in_memory_sqlite_shares_one_connection(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    reset_db()

    assert db.manager.engine().pool.size() == 1

    class Live(Model):
        __tablename__ = "dialect_live"

        id: int = Field(primary_key=True)
        note: str = ""

    await db.create_all()
    await Live.create(note="persists on the single shared connection")
    assert await Live.count() == 1

    await db.dispose()


async def test_vector_field_falls_back_to_json_and_search_refuses(monkeypatch):
    """No pgvector on SQLite: the column round-trips as JSON, and the
    capability gate refuses vector search rather than emulating it."""
    from fastplace.errors import SearchCapabilityMissing

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    reset_db()

    assert db.capabilities.supports_vector is False
    assert db.capabilities.supports_full_text is False

    class Memo(Model):
        __tablename__ = "dialect_memos"

        id: int = Field(primary_key=True)
        body: str = ""
        embedding: list[float] = Field(type="vector", dimensions=3)

    await db.create_all()
    await Memo.create(body="fallback", embedding=[0.1, 0.2, 0.3])

    fetched = await Memo.first()
    assert fetched.embedding == [0.1, 0.2, 0.3]

    with pytest.raises(SearchCapabilityMissing):
        await Memo.vector_search([0.1, 0.2, 0.3], limit=1)

    await db.dispose()


def test_migration_env_renders_ddl_in_batch_mode(tmp_path, monkeypatch):
    """SQLite cannot ALTER most columns — the framework-owned Alembic env
    template must switch on render_as_batch for sqlite URLs."""
    (tmp_path / "app" / "modules" / "x" / "models").mkdir(parents=True)
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "x" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "x" / "models" / "__init__.py").write_text("")
    (tmp_path / "config" / "app.py").write_text("APP_NAME = 'DialectApp'\n")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'dialect.sqlite3'}")
    monkeypatch.chdir(tmp_path)

    from fastplace.cli import app as cli_app

    assert runner.invoke(cli_app, ["db:configure"]).exit_code == 0
    env_source = (tmp_path / "database" / "migrations" / "env.py").read_text()
    assert "render_as_batch" in env_source
    assert 'url.startswith("sqlite")' in env_source
