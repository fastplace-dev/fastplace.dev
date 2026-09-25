# tests/cli/test_http_request.py
"""`http:request` ad-hoc request through the full machinery (roadmap spec #6)."""

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

    http:request boots the real app via load_env() + _load_asgi_app, and
    python-dotenv writes the cwd .env's keys straight into the REAL
    os.environ — a mutation no monkeypatch sees or undoes. Snapshot
    before, restore after (verbatim pattern from
    tests/cli/test_cache_cmds.py:26-41).
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich width so long status/body lines never wrap mid-assertion.

    Rich at 80 columns wraps output and breaks substring assertions
    (verbatim pattern from tests/cli/test_doctor_cmd.py:36).
    """
    monkeypatch.setenv("COLUMNS", "200")


def _make_project(tmp_path, monkeypatch):
    (tmp_path / "asgi.py").write_text(
        "from fastplace.http import create_app\napp = create_app()\n"
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.py").write_text(
        'APP_ENV = "local"\nAPP_KEY = "k" * 48\nAPP_URL = "http://fastplace.local"\n'
    )
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "__init__.py").write_text("")
    (tmp_path / "routes" / "web.py").write_text(
        "from fastplace.http import Json, Router\n\n\n"
        "async def home(request):\n"
        "    sess = request.scope.get('session', {})\n"
        "    return Json({'ok': True, 'user': str(sess.get('user_id', None))})\n\n\n"
        "async def me(request):\n"
        "    sess = request.scope.get('session', {})\n"
        "    return Json({'user_id': sess.get('user_id')})\n\n\n"
        "async def accept(request):\n"
        "    return Json({'ok': True})\n\n\n"
        "router = Router()\n"
        "router.get('/', home)\n"
        "router.get('/me', me)\n"
        "router.post('/', accept)\n"
    )
    (tmp_path / ".env").write_text("APP_ENV=local\nAPP_KEY=" + "k" * 48 + "\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_http_request_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "http:request" in result.stdout


def test_get_prints_status_and_body(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["http:request", "GET", "/"])
    assert result.exit_code == 0, result.stdout
    out = _out(result)
    assert "200" in out
    assert "ok" in out  # JSON body echoed


def test_user_flag_authenticates_via_live_store(tmp_path, monkeypatch):
    """Store-steal contract: the forged session MUST ride the app's own
    ServerSessionMiddleware store — a fresh session_store() would mint a
    different memory store and the route would read no user."""
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["http:request", "GET", "/me", "--user", "42"])
    assert result.exit_code == 0, result.stdout
    assert "42" in _out(result)  # route saw the forged user_id


def test_json_flag_prints_headers_and_decoded_body(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["http:request", "GET", "/", "--json"])
    assert result.exit_code == 0, result.stdout
    out = _out(result)
    assert "status" in out.lower() and "headers" in out.lower()
    assert "content-type" in out.lower()


def test_extra_header_sent(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(
        cli_app, ["http:request", "GET", "/", "--header", "X-Probe: sent-by-cli"]
    )
    assert result.exit_code == 0, result.stdout
    # 200 through: assertion of acceptance is the status line itself
    assert "200" in _out(result)


def test_post_with_data_and_csrf(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(
        cli_app,
        [
            "http:request",
            "POST",
            "/",
            "--data",
            '{"payload": 1}',
            "--header",
            "Content-Type: application/json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "200" in _out(result)
