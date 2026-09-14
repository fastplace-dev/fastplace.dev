"""Portable migrations — Alembic migrate/rollback on every backend.

The blueprint's compatibility-matrix "migration" and "rollback" categories
(relational backends only). The framework owns the Alembic env template;
SQLite renders DDL through batch mode, server backends natively.

These tests are synchronous on purpose: the migration CLI drives Alembic
through ``asyncio.run()`` internally, which refuses to nest inside the
pytest-asyncio loop.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from typer.testing import CliRunner

runner = CliRunner()

_BACKENDS = ["sqlite"]
# Backend param → the env var CI/the gating exports. Explicit on purpose:
# "postgresql".upper() would invent TEST_POSTGRESQL_URL, which nothing sets.
_ENV_BY_BACKEND = {"mysql": "TEST_MYSQL_URL", "postgresql": "TEST_POSTGRES_URL"}
for _name, _env in _ENV_BY_BACKEND.items():
    if os.environ.get(_env):
        _BACKENDS.append(_name)


@pytest.fixture(params=_BACKENDS)
def project(tmp_path, monkeypatch, request):
    """A scaffolded Fastplace project with one model, per backend.

    In-memory SQLite cannot survive across CLI invocations, so the sqlite
    leg points at a scratch file instead of the :memory: URL the other
    portable suites use. Server legs reuse the CI-provided TEST_*_URL.
    """
    (tmp_path / "app" / "modules" / "blog" / "models").mkdir(parents=True)
    (tmp_path / "database" / "seeders").mkdir(parents=True)
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "storage").mkdir()

    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "blog" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "blog" / "models" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "blog" / "models" / "post.py").write_text(
        "from fastplace.orm import Field, Model\n"
        "\n"
        "class Post(Model):\n"
        "    __tablename__ = 'port_posts'\n"
        "    id: int = Field(primary_key=True)\n"
        "    title: str\n"
        "    body: str = Field(text=True, default='')\n"
    )
    (tmp_path / "database" / "seeders" / "post_seeder.py").write_text("")
    (tmp_path / "config" / "app.py").write_text("APP_NAME = 'PortApp'\n")

    backend = request.param
    if backend == "sqlite":
        url = f"sqlite+aiosqlite:///{tmp_path / 'port.sqlite3'}"
    else:
        url = os.environ[_ENV_BY_BACKEND[backend]]
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_DRIVER", backend)
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    # Server legs share one CI database, so every run must leave it as it
    # found it — a leftover alembic_version row makes the next project's
    # `make:migration` fail with "Can't locate revision". SQLite legs get a
    # fresh scratch file per test and need nothing.
    if backend != "sqlite":
        _drop_server_state(url)


def _drop_server_state(url: str) -> None:
    """Best-effort drop of this suite's tables + Alembic bookkeeping."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from fastplace.orm.manager import normalize_database_url

    async def _drop() -> None:
        engine = create_async_engine(normalize_database_url(url))
        try:
            async with engine.begin() as conn:
                for table in ("port_posts", "fastplace_migrations", "alembic_version"):
                    await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(_drop())
    except Exception:  # noqa: BLE001 — teardown must never mask the test result
        pass


def _run_coro(coro):
    """Drive one ORM coroutine on a private loop with a fresh db manager —
    aiosqlite connections are loop-bound, so never reuse the CLI's."""
    from fastplace.db import reset_db

    reset_db()

    async def _wrapped():
        from fastplace.db import db

        try:
            return await coro
        finally:
            await db.dispose()

    return asyncio.run(_wrapped())


def test_migrate_then_rollback_round_trip(project):
    from fastplace.cli import app as cli_app

    assert runner.invoke(cli_app, ["db:configure"]).exit_code == 0

    made = runner.invoke(cli_app, ["make:migration", "create_port_posts"])
    assert made.exit_code == 0, made.output
    versions = list((project / "database" / "migrations" / "versions").glob("*.py"))
    assert len(versions) == 1
    assert "port_posts" in versions[0].read_text()

    migrated = runner.invoke(cli_app, ["migrate"])
    assert migrated.exit_code == 0, migrated.output

    # The migrated table accepts ORM traffic on this backend.
    from app.modules.blog.models.post import Post

    async def seed_one():
        await Post.create(title="live", body="via migration")
        return await Post.count()

    assert _run_coro(seed_one()) == 1

    rolled_back = runner.invoke(cli_app, ["migrate:rollback"])
    assert rolled_back.exit_code == 0, rolled_back.output

    # Table gone — writes now fail at the driver layer on every backend.
    from sqlalchemy.exc import DBAPIError

    async def write_ghost():
        with pytest.raises(DBAPIError):
            await Post.create(title="ghost", body="no table")

    _run_coro(write_ghost())


def test_migration_status_marks_applied_revisions(project):
    """`migration:status` must visually separate applied from pending —
    the marker is a bracketed literal, so it must survive Rich markup."""
    from fastplace.cli import app as cli_app

    assert runner.invoke(cli_app, ["db:configure"]).exit_code == 0
    assert runner.invoke(cli_app, ["make:migration", "add_port_posts"]).exit_code == 0

    pending = runner.invoke(cli_app, ["migration:status"])
    assert pending.exit_code == 0, pending.output
    assert "[ pending]" in pending.output

    assert runner.invoke(cli_app, ["migrate"]).exit_code == 0

    applied = runner.invoke(cli_app, ["migration:status"])
    assert applied.exit_code == 0, applied.output
    assert "[applied]" in applied.output
