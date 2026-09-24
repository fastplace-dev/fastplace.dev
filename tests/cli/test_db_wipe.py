"""``fastplace db:wipe`` — drop every table, including migration state."""

from __future__ import annotations

import sqlite3

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_project_state():
    """Give every test a clean db facade, model metadata, and module cache.

    Same rationale as ``tests/cli/test_migrate_family.py``: seeders import
    project models through package resolution, so foreign cached ``app.*``
    modules must be parked for the test and the pre-test snapshot restored
    afterward (tests/http depends on the repo-owned ``app.*`` staying cached).
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
