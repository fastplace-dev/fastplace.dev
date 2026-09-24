"""``fastplace db:wipe`` — drop every table, including migration state."""

from __future__ import annotations

import sqlite3

import pytest

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from _isolation import isolate_project_state  # noqa: F401
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A scaffolded Fastplace project with one model and a seeder."""
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
    (tmp_path / "database" / "seeders" / "post_seeder.py").write_text(
        "from app.modules.blog.models.post import Post\n"
        "\n"
        "async def run():\n"
        "    await Post.create(title='seeded', body='hello')\n"
    )
    (tmp_path / "config" / "app.py").write_text("APP_NAME = 'TestApp'\n")

    db_file = tmp_path / "test.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _tables(project):
    conn = sqlite3.connect(project / "test.sqlite3")
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _views(project):
    conn = sqlite3.connect(project / "test.sqlite3")
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    finally:
        conn.close()


@pytest.fixture()
def migrated_and_seeded(project):
    assert runner.invoke(cli_app, ["db:configure"]).exit_code == 0
    assert runner.invoke(cli_app, ["make:migration", "create_posts_table"]).exit_code == 0
    assert runner.invoke(cli_app, ["migrate"]).exit_code == 0
    assert runner.invoke(cli_app, ["db:seed"]).exit_code == 0
    assert "posts" in _tables(project)
    return project


def test_db_wipe_force_leaves_zero_tables(migrated_and_seeded):
    result = runner.invoke(cli_app, ["db:wipe", "--force"])

    assert result.exit_code == 0, result.output
    # Downgrade-to-base drops the user tables; the wipe also removes the
    # migration bookkeeping (alembic_version, fastplace_migrations).
    assert _tables(migrated_and_seeded) == set()


def test_db_wipe_refuses_in_production_without_force(migrated_and_seeded, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    result = runner.invoke(cli_app, ["db:wipe"], input="n\n")

    assert result.exit_code == 1
    assert "Wipe" in result.output
    assert "posts" in _tables(migrated_and_seeded)  # untouched


def test_db_wipe_production_confirm_proceeds(migrated_and_seeded, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    result = runner.invoke(cli_app, ["db:wipe"], input="y\n")

    assert result.exit_code == 0, result.output
    assert _tables(migrated_and_seeded) == set()


def test_db_wipe_production_force_skips_the_prompt(migrated_and_seeded, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    result = runner.invoke(cli_app, ["db:wipe", "--force"])

    assert result.exit_code == 0, result.output
    assert "Wipe" not in result.output  # no confirmation prompt
    assert _tables(migrated_and_seeded) == set()


def test_db_wipe_in_testing_env_needs_no_prompt(migrated_and_seeded, monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")

    result = runner.invoke(cli_app, ["db:wipe"])

    assert result.exit_code == 0, result.output
    assert _tables(migrated_and_seeded) == set()


def test_db_wipe_drops_tables_and_views_outside_migration_history(migrated_and_seeded):
    """Spec outcome: a wiped database is EMPTY. Anything downgrade-to-base
    leaves behind — out-of-history tables, stray views, the alembic_version
    bookkeeping — must still be dropped. The stray table also carries a
    dangling foreign key (references a table that never existed), the exact
    shape that breaks dependency-ordered drop strategies."""
    conn = sqlite3.connect(migrated_and_seeded / "test.sqlite3")
    try:
        conn.execute(
            "CREATE TABLE strays (id INTEGER PRIMARY KEY, ghost_id INTEGER REFERENCES ghosts (id))"
        )
        conn.execute("CREATE VIEW stray_view AS SELECT id FROM strays")
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(cli_app, ["db:wipe", "--force"])

    assert result.exit_code == 0, result.output
    assert _tables(migrated_and_seeded) == set()
    assert _views(migrated_and_seeded) == set()


# ---------------------------------------------------------------------------
# migrate:reset / db:reset — the same production guard db:wipe carries
# (final review I-1): both drop the whole schema, so both confirm in
# production unless --force.
# ---------------------------------------------------------------------------


def test_migrate_reset_refuses_in_production_without_force(migrated_and_seeded, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    result = runner.invoke(cli_app, ["migrate:reset"], input="n\n")

    assert result.exit_code == 1
    assert "Reset the production database?" in result.output
    assert "posts" in _tables(migrated_and_seeded)  # schema untouched


def test_migrate_reset_production_force_proceeds(migrated_and_seeded, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    result = runner.invoke(cli_app, ["migrate:reset", "--force"])

    assert result.exit_code == 0, result.output
    assert "Reset the production database?" not in result.output  # no prompt
    assert "posts" not in _tables(migrated_and_seeded)


def test_migrate_reset_in_testing_env_needs_no_prompt(migrated_and_seeded, monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")

    result = runner.invoke(cli_app, ["migrate:reset"])

    assert result.exit_code == 0, result.output
    assert "Reset the production database?" not in result.output
    assert "posts" not in _tables(migrated_and_seeded)


def test_db_reset_refuses_in_production_without_force(migrated_and_seeded, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    result = runner.invoke(cli_app, ["db:reset"], input="n\n")

    assert result.exit_code == 1
    assert "Reset the production database?" in result.output
    assert "posts" in _tables(migrated_and_seeded)  # schema untouched


def test_db_reset_production_force_proceeds_and_reseeds(migrated_and_seeded, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    result = runner.invoke(cli_app, ["db:reset", "--force"])

    assert result.exit_code == 0, result.output
    assert "Reset the production database?" not in result.output  # no prompt
    assert "posts" in _tables(migrated_and_seeded)  # rebuilt…
    seeded = sqlite3.connect(migrated_and_seeded / "test.sqlite3")
    try:
        titles = {row[0] for row in seeded.execute("SELECT title FROM posts")}
    finally:
        seeded.close()
    assert titles == {"seeded"}  # …and re-seeded


def test_db_reset_in_testing_env_needs_no_prompt(migrated_and_seeded, monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")

    result = runner.invoke(cli_app, ["db:reset"])

    assert result.exit_code == 0, result.output
    assert "Reset the production database?" not in result.output
    assert "posts" in _tables(migrated_and_seeded)
