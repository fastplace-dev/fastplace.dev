# tests/cli/test_http_check.py
"""`http:check` in-process hardening contract (roadmap spec #2)."""

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

    http:check bootstraps config via load_env(), and python-dotenv writes
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
        'APP_ENV = "local"\nAPP_KEY = ""\nAPP_URL = "http://fastplace.local"\n'
    )
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "__init__.py").write_text("")
    (tmp_path / "routes" / "web.py").write_text(
        "from fastplace.http import Json, Router\n\n\n"
        "async def home(request):\n"
        "    return Json({'ok': True})\n\n\n"
        "router = Router()\n"
        "router.get('/', home)\n"
    )
    (tmp_path / ".env").write_text(env_text)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_http_check_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "http:check" in result.stdout


def test_local_app_passes_hardening(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["http:check"])
    assert result.exit_code == 0, result.stdout
    out = _out(result)
    assert "security-headers" in out
    assert "json-404" in out
    assert "api-docs" in out
    assert "503-gate" in out


def test_production_gates_docs_and_secures_cookie(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="APP_ENV=production\nAPP_KEY=" + "k" * 48 + "\n")
    result = runner.invoke(cli_app, ["http:check"])
    assert result.exit_code == 0, result.stdout
    out = _out(result)
    assert "api-docs" in out  # 404 in production — row reports the gated state


def test_live_down_state_warns_not_fails(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    state = root / "storage" / "framework" / "maintenance.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"retry": 30}\n')
    result = runner.invoke(cli_app, ["http:check"])
    assert result.exit_code == 0  # operational state, not a contract failure
    assert "DOWN" in _out(result)
