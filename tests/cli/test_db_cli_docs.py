"""Task 19 — `db:cli` (native shell) and `db:documents` (Mongo status).

`db:cli` never shells out in tests: subprocess.run is monkeypatched so the
captured argv can be asserted (binary + resolved path) without an installed
client. `db:documents` swaps the documents factory for a fake database so no
pymongo/Mongo server is needed.
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
def sqlite_project(tmp_path, monkeypatch):
    """A tmp project configured for a file-backed sqlite database.

    No migrations needed — db:cli only resolves config into an argv, and
    subprocess.run is monkeypatched per test.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.py").write_text("APP_NAME = 'TestApp'\n")
    db_file = tmp_path / "shell.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.delenv("DATABASE_DRIVER", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class _Proc:
    """CompletedProcess stand-in with a configurable exit code."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode


@pytest.fixture()
def captured_argv(monkeypatch):
    """Capture what db:cli would exec; record argv, return the given code."""
    state = {"argv": None, "returncode": 0}

    def fake_run(argv, *args, **kwargs):
        state["argv"] = list(argv)
        return _Proc(state["returncode"])

    monkeypatch.setattr("fastplace.cli.db_inspect.subprocess.run", fake_run)
    return state


def test_db_cli_execs_sqlite3_with_the_resolved_path(sqlite_project, captured_argv):
    result = runner.invoke(cli_app, ["db:cli"])

    assert result.exit_code == 0, result.output
    assert captured_argv["argv"] == [
        "sqlite3",
        str(sqlite_project / "shell.sqlite3"),
    ]


def test_db_cli_forwards_the_shell_exit_code(sqlite_project, captured_argv):
    captured_argv["returncode"] = 3

    result = runner.invoke(cli_app, ["db:cli"])

    assert result.exit_code == 3


def test_db_cli_missing_client_binary_exits_127(sqlite_project, monkeypatch):
    def missing_run(argv, *args, **kwargs):
        raise FileNotFoundError("No such file or directory: 'sqlite3'")

    monkeypatch.setattr("fastplace.cli.db_inspect.subprocess.run", missing_run)

    result = runner.invoke(cli_app, ["db:cli"])

    out = ANSI_RE.sub("", result.output)
    assert result.exit_code == 127
    assert "not found" in out


def test_db_cli_unknown_driver_exits_one(tmp_path, monkeypatch, captured_argv):
    # Both keys explicit: a project's config/database.py usually declares
    # DATABASE_DRIVER = 'sqlite' as the default, which would win over the
    # URL's scheme once the env var is gone (config precedence: explicit
    # driver key > URL-derived driver, the manager's own rule).
    monkeypatch.setenv("DATABASE_URL", "mongodb://localhost:27017/fastplace")
    monkeypatch.setenv("DATABASE_DRIVER", "mongodb")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["db:cli"])

    out = ANSI_RE.sub("", result.output)
    assert result.exit_code == 1
    assert "mongodb" in out
    assert captured_argv["argv"] is None  # nothing was exec'd


def test_db_cli_psql_argv_carries_no_password(tmp_path, monkeypatch, captured_argv):
    # Driver explicit — the repo config default ('sqlite') would otherwise
    # shadow the URL's scheme (see the unknown-driver test above).
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://appuser:secret@db.example.com:5432/appdb"
    )
    monkeypatch.setenv("DATABASE_DRIVER", "postgresql")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["db:cli"])

    assert result.exit_code == 0, result.output
    argv = captured_argv["argv"]
    assert argv[0] == "psql"
    # The URL's password must not reach the command line in any form — not
    # the literal secret, and not the '***' display placeholder that psql
    # would otherwise try to authenticate with (audit finding I-1).
    joined = " ".join(argv)
    assert "secret" not in joined
    assert "***" not in joined
    assert "--username=appuser" in argv
    assert "--host=db.example.com" in argv
    assert "--port=5432" in argv
    assert "appdb" in argv  # the database name


def test_db_cli_mysql_argv_carries_no_password(tmp_path, monkeypatch, captured_argv):
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://appuser:secret@db.example.com:3306/appdb")
    monkeypatch.setenv("DATABASE_DRIVER", "mysql")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["db:cli"])

    assert result.exit_code == 0, result.output
    argv = captured_argv["argv"]
    assert argv[0] == "mysql"
    joined = " ".join(argv)
    assert "secret" not in joined
    assert "***" not in joined
    assert "--user=appuser" in argv
    assert "--host=db.example.com" in argv
    assert "--port=3306" in argv
    assert "appdb" in argv


def test_db_documents_without_mongodb_url_prints_disabled_notice(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGODB_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["db:documents"])

    out = ANSI_RE.sub("", result.output)
    assert result.exit_code == 0
    assert "disabled" in out


def test_db_documents_with_url_lists_collections(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MONGODB_URL", "mongodb://localhost:27017/fastplace")

    class FakeCollection:
        def __init__(self, documents: int) -> None:
            self._documents = documents

        async def estimated_document_count(self) -> int:
            return self._documents

    class FakeDatabase:
        async def list_collection_names(self) -> list[str]:
            return ["posts", "zeta_events"]

        def __getitem__(self, name: str) -> FakeCollection:
            return FakeCollection(7 if name == "posts" else 0)

    # The factory import is deferred inside documents_status, so patching the
    # module attribute is enough — no pymongo or server is contacted.
    monkeypatch.setattr("fastplace.orm.documents.documents_database", lambda: FakeDatabase())

    result = runner.invoke(cli_app, ["db:documents"])

    out = ANSI_RE.sub("", result.output)
    assert result.exit_code == 0, out
    assert "posts" in out
    assert "zeta_events" in out
    assert "7" in out  # the per-collection document count
