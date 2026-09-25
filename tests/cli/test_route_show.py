"""`route:show` single-route deep-dive (roadmap spec #4)."""

from __future__ import annotations

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

    route:show bootstraps config via load_env() before importing the
    routers, and python-dotenv writes the cwd .env's keys straight into
    the REAL os.environ — a mutation no monkeypatch sees or undoes.
    Snapshot before, restore after (verbatim pattern from
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
    (tmp_path / "asgi.py").write_text("from fastplace.http import create_app\napp = create_app()\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.py").write_text(
        'APP_ENV = "local"\nAPP_KEY = "k" * 48\nAPP_URL = "http://fastplace.local"\n'
        'ROUTE_MIDDLEWARE = {"throttle": "fastplace.ratelimit.ThrottleMiddleware"}\n'
    )
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "__init__.py").write_text("")
    (tmp_path / "routes" / "web.py").write_text(
        "from starlette.responses import JSONResponse\n"
        "from fastplace.http import Router\n\n"
        "router = Router()\n\n\n"
        "async def show_item(request):\n"
        "    return JSONResponse({'ok': True})\n\n\n"
        "router.get('/items/{item_id}', show_item, middleware=['throttle:5,60'])\n"
    )
    (tmp_path / ".env").write_text("APP_ENV=local\nAPP_KEY=" + "k" * 48 + "\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_route_show_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "route:show" in result.stdout


def test_exact_path_deep_dive(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["route:show", "/items/{item_id}"])
    assert result.exit_code == 0, result.stdout
    out = _out(result)
    assert "GET" in out
    assert "show_item" in out  # handler qualname
    assert "routes.web" in out  # handler module
    assert "throttle:5,60" in out  # alias tuple, declaration order
    assert "throttle" in out  # resolved middleware chain


def test_param_pattern_fallback_matches_concrete_path(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["route:show", "/items/42"])
    assert result.exit_code == 0, result.stdout
    assert "show_item" in _out(result)


def test_unknown_path_exits_one_with_guidance(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["route:show", "/nope"])
    assert result.exit_code == 1
    assert "route:list" in _out(result)  # points at the listing command
