"""db:export — driver-aware database backup (roadmap B2)."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

# Autouse fixture: clean db/model/module/environ state per test (see
# _isolation.py — the environ confinement there replaced this file's own).
from _isolation import isolate_project_state  # noqa: F401  (reset_db + module parking)
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _out(result) -> str:
    return ANSI_RE.sub("", result.output)


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/x.db")
    connection = sqlite3.connect(tmp_path / "x.db")
    with connection:
        connection.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO posts (title) VALUES ('hello')")
    connection.close()
    return tmp_path


def test_sqlite_export_creates_valid_backup(seeded):
    result = runner.invoke(cli_app, ["db:export"])
    assert result.exit_code == 0, result.output
    backups = sorted((seeded / "storage" / "backups").glob("sqlite-*.sqlite3"))
    assert len(backups) == 1
    assert re.match(r"sqlite-\d{8}-\d{6}\.sqlite3", backups[0].name)
    assert backups[0].stat().st_size > 0
    probe = sqlite3.connect(backups[0])
    try:
        rows = probe.execute("SELECT title FROM posts").fetchall()
    finally:
        probe.close()
    assert rows == [("hello",)]


# Review Focus 3 — pinned here.
def test_same_second_collision_gets_suffix(seeded):
    first = runner.invoke(cli_app, ["db:export"])
    second = runner.invoke(cli_app, ["db:export"])
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    backups = sorted((seeded / "storage" / "backups").glob("sqlite-*"))
    assert len(backups) == 2
    assert backups[0].name != backups[1].name


def test_reports_destination_and_size(seeded):
    result = runner.invoke(cli_app, ["db:export"])
    assert result.exit_code == 0, result.output
    assert "storage/backups" in _out(result)


def test_pg_dump_argv_and_missing_binary(seeded, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pw@localhost:5432/app")

    calls = []

    class _FakeResult:
        returncode = 0

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _FakeResult()

    monkeypatch.setattr("fastplace.cli.db_ops.subprocess.run", fake_run)
    result = runner.invoke(cli_app, ["db:export"])
    assert result.exit_code == 0, result.output
    assert calls, "pg_dump should have run"
    argv = calls[0][0]
    assert argv[0] == "pg_dump"
    assert "--file" in argv
    dest = Path(argv[argv.index("--file") + 1])
    assert dest.suffix == ".sql"

    def missing(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr("fastplace.cli.db_ops.subprocess.run", missing)
    result = runner.invoke(cli_app, ["db:export"])
    assert result.exit_code == 127
    assert "pg_dump" in _out(result)
    assert "install the client or add it to PATH" in _out(result)


def test_mysqldump_writes_file(seeded, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://user:pw@localhost:3306/app")

    def fake_run(argv, **kwargs):
        dest = kwargs.get("stdout")
        if dest is not None:
            dest.write(b"-- mysqldump output\n")

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr("fastplace.cli.db_ops.subprocess.run", fake_run)
    result = runner.invoke(cli_app, ["db:export"])
    assert result.exit_code == 0, result.output
    backups = sorted((seeded / "storage" / "backups").glob("mysql-*.sql"))
    assert len(backups) == 1
    assert b"mysqldump" in backups[0].read_bytes()


# Review fix 3 — dump-client credentials travel in the child's
# environment, never in argv (visible to every local account via ps
# for the lifetime of the dump).
def test_pg_dump_password_never_in_argv(seeded, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:hush@localhost:5432/app")

    calls = []

    class _FakeResult:
        returncode = 0

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _FakeResult()

    monkeypatch.setattr("fastplace.cli.db_ops.subprocess.run", fake_run)
    result = runner.invoke(cli_app, ["db:export"])
    assert result.exit_code == 0, result.output
    argv = calls[0][0]
    assert "hush" not in " ".join(argv)
    env = calls[0][1].get("env") or {}
    assert env.get("PGPASSWORD") == "hush"


def test_mysqldump_password_never_in_argv(seeded, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://user:hush@localhost:3306/app")

    calls = []

    class _FakeResult:
        returncode = 0

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        dest = kwargs.get("stdout")
        if dest is not None:
            dest.write(b"-- mysqldump output\n")
        return _FakeResult()

    monkeypatch.setattr("fastplace.cli.db_ops.subprocess.run", fake_run)
    result = runner.invoke(cli_app, ["db:export"])
    assert result.exit_code == 0, result.output
    argv = calls[0][0]
    assert "hush" not in " ".join(argv)
    assert not any(part.startswith("--password") for part in argv)
    env = calls[0][1].get("env") or {}
    assert env.get("MYSQL_PWD") == "hush"


# mongo-G2 — the document store is a first-class citizen: a configured
# MONGODB_URL rides every export, beside the relational dump.
def test_mongodb_url_also_gets_dumped_beside_the_relational_export(seeded, monkeypatch):
    monkeypatch.setenv("MONGODB_URL", "mongodb://localhost:27017/appdb")

    calls = []

    class _FakeResult:
        returncode = 0

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _FakeResult()

    monkeypatch.setattr("fastplace.cli.db_ops.subprocess.run", fake_run)
    result = runner.invoke(cli_app, ["db:export"])
    assert result.exit_code == 0, result.output
    dump_argv = next((a for a in calls if a[0] == "mongodump"), None)
    assert dump_argv is not None, "mongodump must run when MONGODB_URL is set"
    assert "--uri=mongodb://localhost:27017/appdb" in dump_argv
    # Both stores exported in one invocation — the sqlite backup still landed.
    assert sorted((seeded / "storage" / "backups").glob("sqlite-*.sqlite3")), (
        "the relational export must keep running beside the mongo dump"
    )


def test_mongodump_uri_password_prints_the_process_list_note(seeded, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pw@localhost:5432/app")
    monkeypatch.setenv("MONGODB_URL", "mongodb://svc:hush@localhost:27017/appdb")

    class _FakeResult:
        returncode = 0

    def fake_run(argv, **kwargs):
        return _FakeResult()

    monkeypatch.setattr("fastplace.cli.db_ops.subprocess.run", fake_run)
    result = runner.invoke(cli_app, ["db:export"])
    assert result.exit_code == 0, result.output
    assert "process list" in _out(result)


def test_no_mongodb_url_leaves_the_relational_export_alone(seeded, monkeypatch):
    """Without MONGODB_URL nothing reaches for mongodump (sqlite VACUUM path)."""
    result = runner.invoke(cli_app, ["db:export"])
    assert result.exit_code == 0, result.output
    assert "mongodump" not in _out(result)


def test_relational_report_survives_a_mongodump_failure(seeded, monkeypatch):
    """A failed Mongo dump must not swallow the relational export report.

    The relational backup exists on disk before the document-store leg
    runs; the operator must see its path even when that second leg dies.
    """
    monkeypatch.setenv("MONGODB_URL", "mongodb://localhost:27017/appdb")

    def fake_run(argv, **kwargs):
        class _Result:
            def __init__(self, code):
                self.returncode = code
                self.stderr = b"connection refused"

        return _Result(1) if argv[0] == "mongodump" else _Result(0)

    monkeypatch.setattr("fastplace.cli.db_ops.subprocess.run", fake_run)
    result = runner.invoke(cli_app, ["db:export"])
    out = _out(result)
    assert result.exit_code == 1  # the failed dump is still an error
    assert "sqlite-" in out  # ...but the relational report printed first
    assert "exported" in out
