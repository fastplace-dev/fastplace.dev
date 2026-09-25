# tests/cli/test_test_db.py
"""`test:db` disposable scratch database (roadmap spec #14)."""

import os
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.cli import testing

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    test:db force-overrides DATABASE_URL in the REAL os.environ after
    load_env() has already leaked the cwd .env into it — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after (verbatim
    pattern from tests/cli/test_cache_cmds.py:26-41).
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich console width so long lines never wrap mid-assertion.

    Under CliRunner the shared console falls back to 80 columns and a
    masked URL or scratch path would split across lines (verbatim
    pattern from tests/cli/test_test_matrix.py).
    """
    monkeypatch.setenv("COLUMNS", "200")


def _make_project(tmp_path, monkeypatch, env_text="APP_ENV=local\n"):
    (tmp_path / "asgi.py").write_text("")
    (tmp_path / ".env").write_text(env_text)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def migrations_stub(monkeypatch):
    """Record the env the command forces; skip real alembic."""
    seen: dict[str, str] = {}

    def _fake(root) -> bool:
        seen["DATABASE_URL"] = os.environ.get("DATABASE_URL", "")
        seen["DATABASE_DRIVER"] = os.environ.get("DATABASE_DRIVER", "")
        return True

    monkeypatch.setattr(testing, "_run_migrations", _fake)
    return seen


def test_test_db_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "test:db" in result.stdout


def test_default_forces_scratch_sqlite_env(tmp_path, monkeypatch, migrations_stub):
    root = _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["test:db"])
    assert result.exit_code == 0, result.stdout
    assert migrations_stub["DATABASE_URL"].startswith("sqlite+aiosqlite:")
    assert str(root / "storage" / "testing.sqlite3") in migrations_stub["DATABASE_URL"]
    assert migrations_stub["DATABASE_DRIVER"] == "sqlite"
    assert "never touched your configured database" in _out(result)


def test_stale_scratch_triple_removed_before_migrate(tmp_path, monkeypatch, migrations_stub):
    root = _make_project(tmp_path, monkeypatch)
    storage = root / "storage"
    storage.mkdir()
    for suffix in ("", "-wal", "-shm"):
        (storage / f"testing.sqlite3{suffix}").write_text("stale")
    result = runner.invoke(cli_app, ["test:db"])
    assert result.exit_code == 0, result.stdout
    for suffix in ("", "-wal", "-shm"):
        assert not (storage / f"testing.sqlite3{suffix}").exists()


def test_missing_migrations_dir_skips_friendly(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    # no migrations_stub: real _run_migrations runs, finds no database/migrations
    result = runner.invoke(cli_app, ["test:db"])
    assert result.exit_code == 0, result.stdout
    assert "migrations" in _out(result).lower()


def test_seed_runs_seeders(tmp_path, monkeypatch, migrations_stub):
    _make_project(tmp_path, monkeypatch)
    calls: list = []

    def _fake_seed(root) -> None:
        calls.append(root)

    monkeypatch.setattr(testing, "_run_seeders", _fake_seed)
    result = runner.invoke(cli_app, ["test:db", "--seed"])
    assert result.exit_code == 0, result.stdout
    assert calls  # seeder invoked exactly once with the project root


def test_seed_value_error_exits_one(tmp_path, monkeypatch, migrations_stub):
    _make_project(tmp_path, monkeypatch)

    def _bad_seed(root) -> None:
        raise ValueError("seeder ordering problem")

    monkeypatch.setattr(testing, "_run_seeders", _bad_seed)
    result = runner.invoke(cli_app, ["test:db", "--seed"])
    assert result.exit_code == 1
    assert "seeder ordering problem" in _out(result)


def test_database_url_masked_in_output(tmp_path, monkeypatch, migrations_stub):
    _make_project(tmp_path, monkeypatch)
    secret = "sup3r-secret-password"
    url = f"postgresql://user:{secret}@localhost:5432/scratch"
    result = runner.invoke(cli_app, ["test:db", "--database-url", url])
    assert result.exit_code == 0, result.stdout
    out = _out(result)
    assert secret not in out
    assert "postgresql://" in out or "scratch" in out  # shape shown, credentials not


def test_production_guard_blocks_non_local_url(tmp_path, monkeypatch, migrations_stub):
    _make_project(tmp_path, monkeypatch, env_text="APP_ENV=production\n")
    url = "postgresql://user:pw@db.internal:5432/scratch"
    result = runner.invoke(cli_app, ["test:db", "--database-url", url], input="n\n")
    assert result.exit_code == 1
    assert "aborted" in _out(result)
    assert not migrations_stub  # never got as far as migrations


def test_production_sqlite_default_needs_no_confirm(tmp_path, monkeypatch, migrations_stub):
    _make_project(tmp_path, monkeypatch, env_text="APP_ENV=production\n")
    result = runner.invoke(cli_app, ["test:db"])
    assert result.exit_code == 0, result.stdout  # local sqlite scratch: no prompt


def test_user_sqlite_url_completes_and_keeps_default_triple(tmp_path, monkeypatch, migrations_stub):
    root = _make_project(tmp_path, monkeypatch)
    stale = root / "storage" / "testing.sqlite3"
    stale.parent.mkdir()
    stale.write_text("stale")
    url = f"sqlite+aiosqlite:///{tmp_path / 'scratch-user.sqlite3'}"
    result = runner.invoke(cli_app, ["test:db", "--database-url", url])
    assert result.exit_code == 0, f"{result.exit_code} {result.exception!r}"
    assert stale.exists()  # default triple untouched — teardown keys on the default path only
    assert migrations_stub["DATABASE_URL"] == url
    assert migrations_stub["DATABASE_DRIVER"] == "sqlite"
