"""Task 18 — `db:show` and `db:table`: live schema inspection (spec #40, #41).

Both commands read the migrated database through the db facade +
sqlalchemy.inspect; tests run them against a scaffolded tmp sqlite project
(the db:wipe fixture shape) and assert on CLI output.
"""

from __future__ import annotations

import re

import pytest

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from _isolation import isolate_project_state  # noqa: F401
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Rich colorizes output when the environment forces color; strip codes so
# assertions match on plain text.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    """A migrated tmp project: posts table (slug indexed) with one seeded row."""
    (tmp_path / "app" / "modules" / "blog" / "models").mkdir(parents=True)
    (tmp_path / "database" / "seeders").mkdir(parents=True)
    (tmp_path / "config").mkdir(parents=True)

    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "blog" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "blog" / "models" / "__init__.py").write_text("")
    # slug carries an explicit index so db:table has an index to show
    # (sqlite inline UNIQUE constraints are invisible to get_indexes).
    (tmp_path / "app" / "modules" / "blog" / "models" / "post.py").write_text(
        "from fastplace.orm import Field, Model\n"
        "\n"
        "class Post(Model):\n"
        "    __tablename__ = 'posts'\n"
        "    id: int = Field(primary_key=True)\n"
        "    slug: str = Field(index=True)\n"
        "    title: str\n"
        "    body: str = Field(text=True, default='')\n"
    )
    (tmp_path / "database" / "seeders" / "post_seeder.py").write_text(
        "from app.modules.blog.models.post import Post\n"
        "\n"
        "async def run():\n"
        "    await Post.create(slug='first', title='seeded', body='hello')\n"
    )
    (tmp_path / "config" / "app.py").write_text("APP_NAME = 'TestApp'\n")

    db_file = tmp_path / "inspect.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.chdir(tmp_path)

    assert runner.invoke(cli_app, ["db:configure"]).exit_code == 0
    assert runner.invoke(cli_app, ["make:migration", "create_posts_table"]).exit_code == 0
    assert runner.invoke(cli_app, ["migrate"]).exit_code == 0
    assert runner.invoke(cli_app, ["db:seed"]).exit_code == 0
    return tmp_path


def test_db_show_prints_driver_database_and_tables(migrated):
    result = runner.invoke(cli_app, ["db:show"])
    out = ANSI_RE.sub("", result.output)

    assert result.exit_code == 0, out
    assert "sqlite" in out  # the driver
    assert "inspect.sqlite3" in out  # the database name
    assert "posts" in out  # the table list
    # No --counts: names only, no per-table row numbers next to them.
    assert not re.search(r"posts\s+\d", out)


def test_db_show_counts_adds_row_counts(migrated):
    result = runner.invoke(cli_app, ["db:show", "--counts"])
    out = ANSI_RE.sub("", result.output)

    assert result.exit_code == 0, out
    assert "posts" in out
    # The seeder wrote exactly one post — its count renders beside the name.
    assert re.search(r"posts\s+1\b", out)


def test_db_table_prints_columns_types_and_indexes(migrated):
    result = runner.invoke(cli_app, ["db:table", "posts"])
    out = ANSI_RE.sub("", result.output)

    assert result.exit_code == 0, out
    for column in ("id", "slug", "title", "body"):
        assert column in out
    assert "INTEGER" in out  # id's type
    assert "VARCHAR" in out  # slug/title's type
    assert "ix_posts_slug" in out  # the declared index


def test_db_table_missing_table_exits_one(migrated):
    result = runner.invoke(cli_app, ["db:table", "missing"])

    assert result.exit_code == 1
    assert "missing" in ANSI_RE.sub("", result.output)
