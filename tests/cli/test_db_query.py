"""db:query — one SQL statement through the app engine (roadmap A3)."""

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


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    path = tmp_path / "x.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    return path


def _rows(db_file, sql):
    connection = sqlite3.connect(db_file)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def test_select_renders_rows(db_file):
    result = runner.invoke(cli_app, ["db:query", "SELECT 1 AS one"])
    assert result.exit_code == 0, result.output
    assert "one" in _out(result)


def test_named_params_reach_the_statement(db_file):
    runner.invoke(cli_app, ["db:query", "CREATE TABLE t (id INTEGER)", "--execute"])
    runner.invoke(
        cli_app, ["db:query", "INSERT INTO t (id) VALUES (:id)", "--params", '{"id": 3}', "--execute"]
    )
    result = runner.invoke(cli_app, ["db:query", "SELECT id FROM t WHERE id = :id", "--params", '{"id": 3}'])
    assert result.exit_code == 0, result.output
    assert _rows(db_file, "SELECT id FROM t") == [(3,)]


def test_write_without_execute_refused(db_file):
    result = runner.invoke(cli_app, ["db:query", "CREATE TABLE t (id INTEGER)"])
    assert result.exit_code == 1
    assert "refused" in _out(result)
    assert _rows(db_file, "SELECT name FROM sqlite_master WHERE type='table' AND name='t'") == []


def test_write_with_execute_runs_in_testing(db_file):
    result = runner.invoke(cli_app, ["db:query", "CREATE TABLE t (id INTEGER)", "--execute"])
    assert result.exit_code == 0, result.output
    assert _rows(db_file, "SELECT name FROM sqlite_master WHERE type='table' AND name='t'") == [("t",)]


def test_production_guard_triple(db_file, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    declined = runner.invoke(cli_app, ["db:query", "CREATE TABLE a (id INTEGER)", "--execute"], input="n\n")
    assert declined.exit_code == 1
    assert "aborted" in _out(declined)
    assert _rows(db_file, "SELECT name FROM sqlite_master WHERE type='table' AND name='a'") == []
    accepted = runner.invoke(cli_app, ["db:query", "CREATE TABLE a (id INTEGER)", "--execute"], input="y\n")
    assert accepted.exit_code == 0, accepted.output
    forced = runner.invoke(cli_app, ["db:query", "DROP TABLE a", "--execute", "--force"])
    assert forced.exit_code == 0, forced.output
    assert "production database?" not in _out(forced)


def test_dash_reads_stdin(db_file):
    result = runner.invoke(cli_app, ["db:query", "-"], input="SELECT 1 AS from_stdin")
    assert result.exit_code == 0, result.output
    assert "from_stdin" in _out(result)


def test_zero_rows_note(db_file):
    result = runner.invoke(cli_app, ["db:query", "SELECT 1 AS x WHERE 1 = 0"])
    assert result.exit_code == 0, result.output
    assert "0 rows" in _out(result)


# Review Focus 1 + 2 — pinned here.
def test_multi_statement_smuggling_is_write_family(db_file):
    result = runner.invoke(cli_app, ["db:query", "SELECT 1; DELETE FROM sqlite_master"])
    assert result.exit_code == 1
    assert "refused" in _out(result)


def test_string_literal_semicolon_stays_read(db_file):
    result = runner.invoke(cli_app, ["db:query", "SELECT 'a;b' AS literal"])
    assert result.exit_code == 0, result.output
    assert "literal" in _out(result)


def test_params_non_object_json_is_friendly(db_file):
    result = runner.invoke(cli_app, ["db:query", "SELECT :id", "--params", "[1,2]"])
    assert result.exit_code == 1
    out = _out(result)
    assert "object" in out
    assert "Traceback" not in out


def test_bad_params_json_is_friendly(db_file):
    result = runner.invoke(cli_app, ["db:query", "SELECT :id", "--params", "{nope"])
    assert result.exit_code == 1
    assert "Traceback" not in _out(result)


def test_statement_family_unit():
    from fastplace.cli.db_ops import _statement_family

    assert _statement_family("SELECT 1") == "read"
    assert _statement_family("  ( with cte as (select 1) select * from cte )") == "read"
    assert _statement_family("PRAGMA table_info(t)") == "read"
    assert _statement_family("DELETE FROM posts") == "write"
    assert _statement_family("select 1; delete from posts") == "write"
    assert _statement_family("select ';' as a") == "read"
