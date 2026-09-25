# tests/cli/test_doctor_cmd.py
"""`doctor` umbrella pre-flight command (roadmap spec #7)."""

import os
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    doctor bootstraps config via load_env(), and python-dotenv writes the
    cwd .env's keys straight into the REAL os.environ — a mutation no
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
    it falls back to 80 columns and phrases like "defaults only" split
    across cell lines. COLUMNS is read live per render, so pinning it here
    makes every table one-line-per-cell (monkeypatch restores it).
    """
    monkeypatch.setenv("COLUMNS", "200")


def _make_project(tmp_path, monkeypatch, env_text="APP_ENV=local\n"):
    (tmp_path / "asgi.py").write_text("from fastplace.http import create_app\napp = create_app()\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.py").write_text(
        'APP_ENV = "local"\n'
        'APP_KEY = ""\n'
        "SESSION_LIFETIME = 7200\n"
        'CACHE_DRIVER = "memory"\n'
        'QUEUE_DRIVER = "memory"\n'
        'MAIL_DRIVER = "log"\n'
        'AI_MODEL = ""\n'
    )
    if env_text is not None:
        (tmp_path / ".env").write_text(env_text)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_doctor_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "doctor" in result.stdout


def test_production_missing_app_key_fails(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="APP_ENV=production\n")
    result = runner.invoke(cli_app, ["doctor"])
    assert result.exit_code == 1
    assert "app-key" in _out(result)


def test_missing_env_file_passes_with_defaults_note(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text=None)
    result = runner.invoke(cli_app, ["doctor"])
    assert result.exit_code == 0
    assert "defaults only" in _out(result)


def test_duplicate_active_env_keys_fail(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="APP_ENV=local\nAPP_ENV=local\n")
    result = runner.invoke(cli_app, ["doctor"])
    assert result.exit_code == 1
    assert "duplicate" in _out(result)


def test_coercion_failure_warns_but_exits_zero(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="APP_ENV=local\nSESSION_LIFETIME=abc\n")
    result = runner.invoke(cli_app, ["doctor"])
    assert result.exit_code == 0
    assert "SESSION_LIFETIME" in _out(result)


def test_redis_not_used_passes(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["doctor"])
    assert result.exit_code == 0
    assert "not used" in _out(result)


def test_db_failure_fails_without_printing_the_url(tmp_path, monkeypatch):
    _make_project(
        tmp_path,
        monkeypatch,
        env_text=(
            "APP_ENV=local\nDATABASE_URL=sqlite+aiosqlite:////nonexistent-parent/testing.sqlite3\n"
        ),
    )
    result = runner.invoke(cli_app, ["doctor"])
    assert result.exit_code == 1
    out = _out(result)
    assert "database" in out
    assert "nonexistent-parent" not in out


def test_healthy_project_exits_zero(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="APP_ENV=local\nAPP_KEY=" + "k" * 48 + "\n")
    result = runner.invoke(cli_app, ["doctor"])
    assert result.exit_code == 0
    out = _out(result)
    assert "app-key" in out and "env-file" in out and "config-import" in out
