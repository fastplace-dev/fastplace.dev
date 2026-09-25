# tests/cli/test_http_doctor.py
"""`http:doctor` boot-time contract checks (roadmap spec #1)."""

import json
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

    http:doctor bootstraps config via load_env(), and python-dotenv writes
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

    Rich at 80 columns wraps DETAIL cells and breaks substring assertions
    (verbatim pattern from tests/cli/test_doctor_cmd.py:36).
    """
    monkeypatch.setenv("COLUMNS", "200")


def _make_project(tmp_path, monkeypatch, env_text="APP_ENV=local\nAPP_KEY=" + "k" * 48 + "\n"):
    (tmp_path / "asgi.py").write_text(
        "from fastplace.http import create_app\napp = create_app()\n"
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.py").write_text(
        'APP_ENV = "local"\nAPP_KEY = ""\nAPP_URL = ""\n'
    )
    (tmp_path / "config" / "auth.py").write_text('TRUSTED_HOSTS: list[str] = []\n')
    (tmp_path / ".env").write_text(env_text)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_http_doctor_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "http:doctor" in result.stdout


def test_healthy_project_exits_zero(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    (root / "public" / "build").mkdir(parents=True, exist_ok=True)
    (root / "public" / "build" / "manifest.json").write_text("{}")
    result = runner.invoke(cli_app, ["http:doctor"])
    assert result.exit_code == 0
    out = _out(result)
    assert "app-key" in out and "app-url" in out and "maintenance" in out


def test_production_empty_app_key_fails(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="APP_ENV=production\nAPP_KEY=\n")
    result = runner.invoke(cli_app, ["http:doctor"])
    assert result.exit_code == 1
    assert "app-key" in _out(result)


def test_garbage_app_url_fails(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="APP_ENV=local\nAPP_KEY=" + "k" * 48 + "\nAPP_URL=not a url\n")
    result = runner.invoke(cli_app, ["http:doctor"])
    assert result.exit_code == 1
    assert "app-url" in _out(result)


def test_empty_app_url_with_trusted_hosts_warns_not_fails(tmp_path, monkeypatch):
    _make_project(
        tmp_path, monkeypatch,
        env_text="APP_ENV=local\nAPP_KEY=" + "k" * 48 + "\nTRUSTED_HOSTS=localhost\n",
    )
    result = runner.invoke(cli_app, ["http:doctor"])
    assert result.exit_code == 0
    assert "app-url" in _out(result)


def test_corrupt_maintenance_file_warns_fail_closed(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    state = root / "storage" / "framework" / "maintenance.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{not json")
    result = runner.invoke(cli_app, ["http:doctor"])
    assert result.exit_code == 0  # corrupt = WARN row (fail-closed DOWN), not a doctor failure
    out = _out(result)
    assert "fail-closed" in out


def test_down_maintenance_reports_down_state(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    state = root / "storage" / "framework" / "maintenance.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"retry": 60, "refresh": 30, "secret": "hunter2secret"}) + "\n")
    result = runner.invoke(cli_app, ["http:doctor"])
    assert result.exit_code == 0
    out = _out(result)
    assert "down" in out
    assert "hunter2secret" not in out  # secret value never printed


def test_missing_manifest_with_package_json_warns(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    (root / "package.json").write_text('{"name": "x"}\n')
    result = runner.invoke(cli_app, ["http:doctor"])
    assert result.exit_code == 0
    out = _out(result)
    assert "manifest" in out and "node-modules" in out


def test_short_app_key_warns(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="APP_ENV=local\nAPP_KEY=short\n")
    result = runner.invoke(cli_app, ["http:doctor"])
    assert result.exit_code == 0
    assert "32" in _out(result)
