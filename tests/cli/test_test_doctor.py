# tests/cli/test_test_doctor.py
"""`test:doctor` test-environment preflight (roadmap spec #18)."""

import os
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.cli import testing as testing_mod

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    test:doctor bootstraps config via load_env(), and python-dotenv writes
    the cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after (verbatim
    pattern from tests/cli/test_cache_cmds.py:26-41).
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich table width so detail cells never wrap mid-assertion.

    The command renders through the shared global console; under CliRunner
    it falls back to 80 columns and phrases like "not in the portable
    matrix" would split across cell lines. COLUMNS is read live per
    render, so pinning it here makes every table one-line-per-cell
    (monkeypatch restores it).
    """
    monkeypatch.setenv("COLUMNS", "200")


class _Proc:
    def __init__(self, returncode=0, stdout="chromium 1155 present\n"):
        self.returncode = returncode
        self.stdout = stdout


@pytest.fixture(autouse=True)
def _healthy_shell(monkeypatch, tmp_path):
    """Everything green by default: imports OK, binaries found, browsers
    installed, storage writable, local sqlite DB. Individual tests break
    exactly one thing."""
    monkeypatch.setattr(testing_mod, "_probe_import", lambda name: None)
    monkeypatch.setattr("shutil.which", lambda name: f"/bin/{name}" if name in ("node", "npm", "npx") else None, raising=False)
    monkeypatch.setattr(testing_mod, "_subprocess_run", lambda argv, *a, **k: _Proc())
    monkeypatch.setattr(testing_mod, "_probe_write", lambda path: True)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "local")


def _make_project(tmp_path, monkeypatch, env_text="APP_ENV=local\n"):
    (tmp_path / "asgi.py").write_text("")
    (tmp_path / ".env").write_text(env_text)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_test_doctor_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "test:doctor" in result.stdout


def test_healthy_environment_exits_zero(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["test:doctor"])
    assert result.exit_code == 0, result.stdout
    assert "0 fail" in _out(result)


def test_missing_dev_extra_fails_hard(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(testing_mod, "_probe_import", lambda name: "No module named 'x'" if name == "pytest_asyncio" else None)
    result = runner.invoke(cli_app, ["test:doctor"])
    assert result.exit_code == 1
    assert "pytest_asyncio" in _out(result)


def test_missing_matrix_extra_is_info_not_failure(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        testing_mod,
        "_probe_import",
        lambda name: "No module named 'x'" if name in ("asyncpg", "pgvector", "asyncmy", "pymongo", "saq", "redis") else None,
    )
    result = runner.invoke(cli_app, ["test:doctor"])
    assert result.exit_code == 0, result.stdout  # report, not demand


def test_mongo_reported_as_contract_only_not_portable(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["test:doctor"])
    out = _out(result)
    assert "mongodb" in out.lower()
    assert "not in the portable matrix" in out.lower()  # stated plainly


def test_missing_node_is_warn_not_fail(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr("shutil.which", lambda name: None, raising=False)
    result = runner.invoke(cli_app, ["test:doctor"])
    assert result.exit_code == 0, result.stdout
    assert "node" in _out(result).lower()


def test_unwritable_storage_fails(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(testing_mod, "_probe_write", lambda path: False)
    result = runner.invoke(cli_app, ["test:doctor"])
    assert result.exit_code == 1
    assert "storage" in _out(result).lower()


def test_production_database_url_warns_masked(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="APP_ENV=production\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fastplace:sup3rs3cret@db.internal.example/fastplace")
    result = runner.invoke(cli_app, ["test:doctor"])
    out = _out(result)
    assert "sup3rs3cret" not in out  # credentials never printed
    assert "db.internal.example" in out  # host still visible through the mask


def test_remote_database_url_warns_even_in_local(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.remote.example/x")
    result = runner.invoke(cli_app, ["test:doctor"])
    out = _out(result)
    assert "remote" in out or "non-local" in out or "production" in out
    assert ":p@" not in out


def test_local_sqlite_database_url_passes_silent(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///storage/testing.sqlite3")
    result = runner.invoke(cli_app, ["test:doctor"])
    assert result.exit_code == 0, result.stdout
