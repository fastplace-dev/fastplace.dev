"""db:health — probe every configured connection and replica (roadmap A1)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from _isolation import isolate_project_state  # noqa: F401  (reset_db + module parking)
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    db:health bootstraps config via load_env(), and python-dotenv writes
    the cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after: identical
    pattern to the project fixture in test_env_crypt.
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


def _out(result) -> str:
    return ANSI_RE.sub("", result.output)


def test_default_sqlite_connection_is_healthy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/x.db")
    result = runner.invoke(cli_app, ["db:health"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "default" in out
    assert "ok" in out


def test_unreachable_connection_exits_one_but_still_prints_rows(tmp_path, monkeypatch):
    # aiosqlite cannot open a file inside a directory that does not exist
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/no-such-dir/x.db")
    result = runner.invoke(cli_app, ["db:health"])
    assert result.exit_code == 1
    out = _out(result)
    assert "unreachable" in out
    assert "default" in out


def test_replicas_get_their_own_rows(tmp_path, monkeypatch):
    # Real formats, verified against fastplace/orm/manager.py:95-141
    # (`_connections_from_config`): named connections are declared in a
    # project config/database.py dict — the manager requires
    # isinstance(dict), so an env-var JSON string is never parsed — and a
    # "replicas" key inside a DATABASE_CONNECTIONS spec is dropped (only
    # pool keys are copied through). Read replicas ride the default
    # connection via DATABASE_READ_REPLICAS (list, JSON array, or CSV).
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    live = f"sqlite+aiosqlite:///{tmp_path}/x.db"
    dead = f"sqlite+aiosqlite:///{tmp_path}/no-such-dir/y.db"
    (cfg_dir / "database.py").write_text(
        f"DATABASE_CONNECTIONS = {{\n    'analytics': {{'url': '{live}'}},\n}}\n"
    )
    from fastplace.config import reset_config

    repo_root = Path.cwd()  # capture before chdir; restore binding even on failure
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DATABASE_URL", live)
    monkeypatch.setenv("DATABASE_READ_REPLICAS", f'["{dead}"]')
    reset_config(tmp_path)
    try:
        result = runner.invoke(cli_app, ["db:health"])
    finally:
        reset_config(repo_root)
    assert result.exit_code == 1, result.output
    out = _out(result)
    assert "analytics" in out
    assert "replica 1" in out
    assert "ok" in out  # primary probed fine
