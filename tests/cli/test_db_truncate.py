"""db:truncate — catalog-validated, dialect-aware table truncation (roadmap B1)."""

from __future__ import annotations

import os
import re
import sqlite3

import pytest

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from _isolation import isolate_project_state  # noqa: F401  (reset_db + module parking)
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


# The field import is the branch's proven fixture shape (test_migrate_check.py):
# `Field` from fastplace.orm — there is no StringField in this framework.
POST_MODEL = '''\
from fastplace.orm import Field, Model


class Post(Model):
    title: str = Field()
'''

COMMENT_MODEL = POST_MODEL.replace("Post", "Comment").replace("title", "body")


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    (tmp_path / "app" / "modules" / "blog" / "models").mkdir(parents=True)
    (tmp_path / "app" / "modules" / "blog" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "__init__.py").write_text("")
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "blog" / "models" / "post.py").write_text(POST_MODEL)
    (tmp_path / "app" / "modules" / "blog" / "models" / "comment.py").write_text(COMMENT_MODEL)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/x.db")
    # make:migration takes a required name argument (see fastplace/cli/database.py).
    for args in (["db:configure"], ["make:migration", "create_posts_and_comments"], ["migrate"]):
        result = runner.invoke(cli_app, args)
        assert result.exit_code == 0, result.output
    return tmp_path


def _seed(tmp_path):
    connection = sqlite3.connect(tmp_path / "x.db")
    with connection:
        connection.execute("INSERT INTO posts (title) VALUES ('a')")
        connection.execute("INSERT INTO comments (body) VALUES ('b')")
    connection.close()


def _count(tmp_path, table):
    connection = sqlite3.connect(tmp_path / "x.db")
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


def test_truncates_named_tables_leaves_others(migrated, tmp_path):
    _seed(tmp_path)
    result = runner.invoke(cli_app, ["db:truncate", "posts", "comments"])
    assert result.exit_code == 0, result.output
    assert _count(tmp_path, "posts") == 0
    assert _count(tmp_path, "comments") == 0


def test_migration_history_untouched(migrated, tmp_path):
    _seed(tmp_path)
    runner.invoke(cli_app, ["db:truncate", "posts", "comments"])
    assert _count(tmp_path, "alembic_version") == 1


def test_unknown_table_refused_before_any_touch(migrated, tmp_path):
    _seed(tmp_path)
    result = runner.invoke(cli_app, ["db:truncate", "posts", "nope_table"])
    assert result.exit_code == 1
    out = _out(result)
    assert "unknown table 'nope_table'" in out
    assert "posts" in out  # known-tables list offered
    assert _count(tmp_path, "posts") == 1  # nothing truncated


def test_production_guard_triple(migrated, tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    declined = runner.invoke(cli_app, ["db:truncate", "posts"], input="n\n")
    assert declined.exit_code == 1
    assert "aborted" in _out(declined)
    assert _count(tmp_path, "posts") == 1
    accepted = runner.invoke(cli_app, ["db:truncate", "posts"], input="y\n")
    assert accepted.exit_code == 0, accepted.output
    assert _count(tmp_path, "posts") == 0
    monkeypatch.setenv("APP_ENV", "testing")
    _seed(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    forced = runner.invoke(cli_app, ["db:truncate", "posts", "--force"])
    assert forced.exit_code == 0, forced.output
    assert "production database?" not in _out(forced)
    assert _count(tmp_path, "posts") == 0


def test_cascade_flag_accepted_on_sqlite_and_changes_nothing(migrated, tmp_path):
    _seed(tmp_path)
    result = runner.invoke(cli_app, ["db:truncate", "posts", "--cascade"])
    assert result.exit_code == 0, result.output
    assert _count(tmp_path, "posts") == 0


# Statement builder — unit level, no postgres service (roadmap B1 acceptance).
def test_postgres_statement_shape_includes_restart_identity_and_cascade():
    from fastplace.cli.db_ops import _truncate_statements

    statements = _truncate_statements("postgresql", ['"posts"', '"comments"'], cascade=True)
    assert statements == ['TRUNCATE TABLE "posts", "comments" RESTART IDENTITY CASCADE']


def test_postgres_statement_without_cascade():
    from fastplace.cli.db_ops import _truncate_statements

    statements = _truncate_statements("postgresql", ['"posts"'], cascade=False)
    assert statements == ['TRUNCATE TABLE "posts" RESTART IDENTITY']
