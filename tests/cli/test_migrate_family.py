"""The migration command family — status alias, reset, pretend, single-seeder seeding."""

from __future__ import annotations

import sqlite3

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_project_state():
    """Give every test a clean db facade, model metadata, and module cache.

    Seeders import project models (``app.*``) through normal package
    resolution, so any foreign cached ``app`` package — the repo's own
    project or an earlier tmp project — shadows this test's tree, and a
    cached ``Post`` class writes rows into a previous project's engine.
    Foreign ``app.*`` entries are parked for the test's duration and the
    pre-test snapshot is restored afterward (unlike ``tests/orm/conftest.py``
    which blanket-clears: these tests run before ``tests/http``, whose
    repo-owned ``app.*`` must stay cached or a re-import collides in the
    global @Job registry).
    """
    import sys

    from fastplace.db import reset_db
    from fastplace.orm import Model

    reset_db()
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    saved_tables = set(Model.metadata.tables)
    parked = {
        name: module
        for name, module in saved_modules.items()
        if name == "app" or name.startswith(("app.", "_fastplace_seeder_", "_fastplace_config_"))
    }
    for name in parked:
        del sys.modules[name]
    yield
    reset_db()
    for key in set(Model.metadata.tables) - saved_tables:
        Model.metadata.remove(Model.metadata.tables[key])
    for name in [m for m in list(sys.modules) if m not in saved_modules]:
        del sys.modules[name]
    sys.modules.update(parked)
    sys.path[:] = saved_path


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A scaffolded Fastplace project with one model and two seeders."""
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
        "    __tablename__ = 'posts'\n"
        "    id: int = Field(primary_key=True)\n"
        "    title: str\n"
        "    body: str = Field(text=True, default='')\n"
    )
    # Marker rows distinguish which seeder actually ran.
    (tmp_path / "database" / "seeders" / "post_seeder.py").write_text(
        "from app.modules.blog.models.post import Post\n"
        "\n"
        "async def run():\n"
        "    await Post.create(title='post-seeder-ran', body='hello')\n"
    )
    (tmp_path / "database" / "seeders" / "user_seeder.py").write_text(
        "from app.modules.blog.models.post import Post\n"
        "\n"
        "async def run():\n"
        "    await Post.create(title='user-seeder-ran', body='hello')\n"
    )
    (tmp_path / "config" / "app.py").write_text("APP_NAME = 'TestApp'\n")

    db_file = tmp_path / "test.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _rows(project, sql):
    conn = sqlite3.connect(project / "test.sqlite3")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _table_names(project):
    """Sorted table names via the SQLAlchemy inspector (missing file = no tables)."""
    from sqlalchemy import inspect

    db_file = project / "test.sqlite3"
    if not db_file.exists():
        return []
    from sqlalchemy.engine import create_engine

    engine = create_engine(f"sqlite:///{db_file}")
    try:
        return sorted(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _migrated_project(project):
    """Configure migrations, create the posts revision, and apply it."""
    assert runner.invoke(cli_app, ["db:configure"]).exit_code == 0
    assert runner.invoke(cli_app, ["make:migration", "create_posts_table"]).exit_code == 0
    assert runner.invoke(cli_app, ["migrate"]).exit_code == 0


def test_migrate_status_matches_the_legacy_alias_output(project):
    _migrated_project(project)

    canonical = runner.invoke(cli_app, ["migrate:status"])
    legacy = runner.invoke(cli_app, ["migration:status"])

    assert canonical.exit_code == 0, canonical.output
    assert legacy.exit_code == 0, legacy.output
    assert canonical.output == legacy.output
    assert "Migration status:" in canonical.output
    assert "[applied]" in canonical.output


def test_legacy_migration_status_is_hidden_from_help(project):
    """migrate:status is the canonical name; migration:status stays as a hidden alias."""
    help_text = runner.invoke(cli_app, ["--help"]).output

    assert "migrate:status" in help_text
    assert "migration:status" not in help_text


def test_migrate_reset_leaves_zero_applied_revisions(project):
    _migrated_project(project)

    result = runner.invoke(cli_app, ["migrate:reset"])
    assert result.exit_code == 0, result.output

    from fastplace.orm.migrations import MigrationsManager

    status = MigrationsManager(project).status()
    # status() escapes the bracket markers for Rich; raw string keeps the backslash.
    assert "\\[applied]" not in status
    assert "\\[ pending]" in status
    # Downgrade-to-base took the schema with it — and did NOT rebuild or reseed.
    assert _rows(project, "SELECT count(*) FROM sqlite_master WHERE name='posts'") == [(0,)]


def test_migrate_pretend_prints_sql_and_leaves_the_schema_unchanged(project):
    assert runner.invoke(cli_app, ["db:configure"]).exit_code == 0
    assert runner.invoke(cli_app, ["make:migration", "create_posts_table"]).exit_code == 0

    before = _table_names(project)
    result = runner.invoke(cli_app, ["migrate", "--pretend"])

    assert result.exit_code == 0, result.output
    assert "CREATE TABLE" in result.output
    assert _table_names(project) == before
    assert _rows(project, "SELECT count(*) FROM sqlite_master WHERE name='posts'") == [(0,)]


def test_migrate_pretend_on_a_fully_migrated_database_renders_nothing(project):
    """The script covers exactly the pending revisions — applied ones are
    not re-rendered from base."""
    _migrated_project(project)

    result = runner.invoke(cli_app, ["migrate", "--pretend"])

    assert result.exit_code == 0, result.output
    assert "nothing to migrate" in result.output
    assert "CREATE TABLE" not in result.output
    assert _rows(project, "SELECT count(*) FROM sqlite_master WHERE name='posts'") == [(1,)]


def test_db_seed_seeder_runs_only_the_matching_module(project):
    _migrated_project(project)

    result = runner.invoke(cli_app, ["db:seed", "--seeder", "user_seeder"])

    assert result.exit_code == 0, result.output
    assert "user_seeder" in result.output
    assert "post_seeder" not in result.output
    assert {row[0] for row in _rows(project, "SELECT title FROM posts")} == {"user-seeder-ran"}


def test_db_seed_seeder_matches_without_the_seeder_suffix(project):
    _migrated_project(project)

    result = runner.invoke(cli_app, ["db:seed", "--seeder", "user"])

    assert result.exit_code == 0, result.output
    assert {row[0] for row in _rows(project, "SELECT title FROM posts")} == {"user-seeder-ran"}


def test_db_seed_seeder_without_a_match_lists_available_seeders(project):
    result = runner.invoke(cli_app, ["db:seed", "--seeder", "nonexistent"])

    assert result.exit_code == 1
    assert "nonexistent" in result.output
    assert "post_seeder" in result.output
    assert "user_seeder" in result.output
