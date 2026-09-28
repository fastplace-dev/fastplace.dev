"""The db command group must honor the project .env — same config source the
running app sees (kernel loads .env at create_app; the CLI must not diverge).
"""

from __future__ import annotations

import sqlite3

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from tests.cli._isolation import isolate_project_state  # noqa: F401

runner = CliRunner()


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A scaffolded Fastplace project; DATABASE_* deliberately NOT in the shell env."""
    (tmp_path / "app" / "modules" / "blog" / "models").mkdir(parents=True)
    (tmp_path / "database" / "seeders").mkdir(parents=True)
    (tmp_path / "config").mkdir()
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
    )
    (tmp_path / "database" / "seeders" / "post_seeder.py").write_text(
        "from app.modules.blog.models.post import Post\n"
        "\n"
        "async def run():\n"
        "    await Post.create(title='from-seeder')\n"
    )
    (tmp_path / "config" / "app.py").write_text("APP_NAME = 'TestApp'\n")

    # The whole point: the .env FILE is the only place DATABASE_URL lives.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_DRIVER", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _switch_env_file(project, url):
    """Point .env's DATABASE_URL at `url` (db:configure created the file first)."""
    assert runner.invoke(cli_app, ["db:configure"]).exit_code == 0
    (project / ".env").write_text(f"DATABASE_URL={url}\n")


def _tables(db_file):
    conn = sqlite3.connect(db_file)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def _migrated_via_env_file(project):
    _switch_env_file(project, f"sqlite+aiosqlite:///{project / 'from-env.sqlite3'}")
    assert runner.invoke(cli_app, ["make:migration", "create_posts"]).exit_code == 0
    result = runner.invoke(cli_app, ["migrate"])
    assert result.exit_code == 0, result.output


def test_migrate_honors_env_file_database_url(project):
    _migrated_via_env_file(project)

    assert "posts" in _tables(project / "from-env.sqlite3")
    # The silent-wrong-database failure mode: a stray default file appearing
    # next to the project while .env pointed elsewhere.
    assert not (project / "database.sqlite3").exists()


def test_migrate_status_honors_env_file_database_url(project):
    _migrated_via_env_file(project)

    result = runner.invoke(cli_app, ["migrate:status"])

    assert result.exit_code == 0, result.output
    assert "[applied]" in result.output
    assert not (project / "database.sqlite3").exists()


def test_db_seed_honors_env_file_database_url(project):
    _migrated_via_env_file(project)

    result = runner.invoke(cli_app, ["db:seed"])

    assert result.exit_code == 0, result.output
    conn = sqlite3.connect(project / "from-env.sqlite3")
    try:
        titles = {row[0] for row in conn.execute("SELECT title FROM posts")}
    finally:
        conn.close()
    assert titles == {"from-seeder"}
    assert not (project / "database.sqlite3").exists()


def test_first_command_in_a_fresh_process_sees_env_file(project):
    """The registration-level wrapper, not a masked group callback.

    Typer drops a sub-app's callback under ``add_typer(name="")``, so the
    load must ride command registration. This is the fresh-CLI-process
    shape: the FIRST db command invoked must already see .env — no earlier
    in-process call may have primed os.environ. `db:seed` without prior
    migrations fails on the missing table; the assertion is the process
    environment, which only the wrapper (run before the command body)
    could have set.
    """
    import os

    _switch_env_file(project, f"sqlite+aiosqlite:///{project / 'fresh.sqlite3'}")
    runner.invoke(cli_app, ["db:seed"])

    assert os.environ.get("DATABASE_URL") == f"sqlite+aiosqlite:///{project / 'fresh.sqlite3'}"
    assert not (project / "database.sqlite3").exists()


def test_real_environment_still_wins_over_env_file(project, monkeypatch):
    """load_dotenv(override=False): an exported DATABASE_URL beats .env."""
    _switch_env_file(project, f"sqlite+aiosqlite:///{project / 'from-env.sqlite3'}")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{project / 'from-shell.sqlite3'}")

    assert runner.invoke(cli_app, ["make:migration", "create_posts"]).exit_code == 0
    result = runner.invoke(cli_app, ["migrate"])
    assert result.exit_code == 0, result.output

    assert "posts" in _tables(project / "from-shell.sqlite3")
    assert not (project / "from-env.sqlite3").exists()
    assert not (project / "database.sqlite3").exists()
