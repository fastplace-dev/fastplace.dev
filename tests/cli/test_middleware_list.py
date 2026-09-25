"""`middleware:list` resolved onion + route-alias table (roadmap spec #3)."""

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

    middleware:list bootstraps config via load_env() before booting the
    app, and python-dotenv writes the cwd .env's keys straight into the
    REAL os.environ — a mutation no monkeypatch sees or undoes. Snapshot
    before, restore after (verbatim pattern from
    tests/cli/test_cache_cmds.py:26-41).
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


def _make_project(tmp_path, monkeypatch):
    (tmp_path / "asgi.py").write_text(
        "from fastplace.http import create_app\napp = create_app()\n"
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.py").write_text(
        'APP_ENV = "local"\nAPP_KEY = "k" * 48\nAPP_URL = "http://fastplace.local"\n'
        'ROUTE_MIDDLEWARE = {"throttle": "fastplace.ratelimit.ThrottleMiddleware"}\n'
    )
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "__init__.py").write_text("")
    (tmp_path / "routes" / "web.py").write_text(
        "from starlette.responses import JSONResponse\n\n\n"
        "async def home(request):\n"
        "    return JSONResponse({'ok': True})\n\n\n"
        "routes = [home]\n"
    )
    (tmp_path / ".env").write_text("APP_ENV=local\nAPP_KEY=" + "k" * 48 + "\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_middleware_list_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "middleware:list" in result.stdout


def test_renders_onion_outermost_first(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["middleware:list"])
    assert result.exit_code == 0, result.stdout
    out = _out(result)
    # ServerErrorMiddleware is Starlette's outermost frame — printed first.
    assert out.index("ServerErrorMiddleware") < out.index("ExceptionMiddleware")
    # Position column present and starts at the outermost layer.
    assert re.search(r"0.*ServerErrorMiddleware", out, re.DOTALL)
    # A real kernel middleware appears with its module-qualified class name.
    assert "fastplace." in out and "Middleware" in out


def test_renders_route_alias_table(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["middleware:list"])
    assert result.exit_code == 0, result.stdout
    out = _out(result)
    assert "ROUTE MIDDLEWARE" in out  # the alias section header
    # The registry's aliases each print with a resolved class qualname.
    assert re.search(r"throttle.*Middleware", out, re.DOTALL)
