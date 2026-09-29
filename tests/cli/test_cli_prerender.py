"""`fastplace prerender` — CLI contract: capture, write, report, exit codes."""

from __future__ import annotations

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
    """The command loads the project .env into the real environ; snapshot it."""
    env_before = dict(os.environ)
    os.environ.pop("PRERENDER_ROUTES", None)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")


def _make_project(tmp_path, monkeypatch, routes_body=""):
    (tmp_path / "asgi.py").write_text(
        f"from fastplace.http import create_app\napp = create_app()\n{routes_body}"
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.py").write_text(
        'APP_ENV = "local"\nAPP_KEY = "k" * 48\nAPP_URL = "http://fastplace.local"\n'
    )
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "__init__.py").write_text("")
    (tmp_path / "routes" / "web.py").write_text(
        "from fastplace.http import Html, Router\n\n\n"
        "async def home(request):\n"
        "    return Html('<h1>HOME-PAGE</h1>')\n\n\n"
        "async def gone(request):\n"
        "    return Html('<h1>gone</h1>', status_code=404)\n\n\n"
        "router = Router()\n"
        "router.get('/', home)\n"
        "router.get('/gone', gone)\n"
    )
    (tmp_path / ".env").write_text("APP_ENV=local\nAPP_KEY=" + "k" * 48 + "\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_prerender_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "prerender" in result.stdout


def test_happy_path_captures_root_and_writes_manifest(tmp_path, monkeypatch):
    project = _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["prerender"])
    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "/" in out
    assert "1 page" in out  # summary line carries the count
    html = (project / "public" / "build" / "prerender" / "index.html").read_bytes()
    assert b"HOME-PAGE" in html
    manifest = json.loads(
        (project / "public" / "build" / "prerender" / "prerender-manifest.json").read_text()
    )
    assert manifest["routes"] == ["/"]
    assert manifest["skipped"] == []


def test_route_flag_overrides_and_skips_are_reported(tmp_path, monkeypatch):
    project = _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["prerender", "--route", "/gone"])
    assert result.exit_code == 0, _out(result)
    manifest = json.loads(
        (project / "public" / "build" / "prerender" / "prerender-manifest.json").read_text()
    )
    assert manifest["routes"] == []
    assert manifest["skipped"] == ["/gone"]
    assert not (project / "public" / "build" / "prerender" / "gone").exists()


def test_module_attr_routes_used(tmp_path, monkeypatch):
    project = _make_project(
        tmp_path, monkeypatch, routes_body="PRERENDER_ROUTES = ['/', '/gone']\n"
    )
    result = runner.invoke(cli_app, ["prerender"])
    assert result.exit_code == 0, _out(result)
    manifest = json.loads(
        (project / "public" / "build" / "prerender" / "prerender-manifest.json").read_text()
    )
    assert manifest["routes"] == ["/"]
    assert manifest["skipped"] == ["/gone"]


def test_zero_routes_exits_zero_and_leaves_existing_output(tmp_path, monkeypatch):
    """An app that configured zero routes exits 0 without touching output."""
    project = _make_project(tmp_path, monkeypatch)
    # First run captures something.
    assert runner.invoke(cli_app, ["prerender"]).exit_code == 0
    marker = project / "public" / "build" / "prerender" / "index.html"
    assert marker.exists()
    # PRERENDER_ROUTES = [] on the asgi module disables prerendering.
    (project / "asgi.py").write_text(
        "from fastplace.http import create_app\napp = create_app()\nPRERENDER_ROUTES = []\n"
    )
    result = runner.invoke(cli_app, ["prerender"])
    assert result.exit_code == 0, _out(result)
    assert "Nothing to prerender" in _out(result)
    assert marker.exists()  # untouched


def test_app_import_failure_exits_one(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    (tmp_path / "asgi.py").write_text("raise ImportError('broken asgi')\n")
    result = runner.invoke(cli_app, ["prerender"])
    assert result.exit_code == 1
    assert "broken asgi" in _out(result) or result.exception is not None


def test_out_flag_targets_custom_directory(tmp_path, monkeypatch):
    project = _make_project(tmp_path, monkeypatch)
    custom = tmp_path / "elsewhere"
    result = runner.invoke(cli_app, ["prerender", "--out", str(custom)])
    assert result.exit_code == 0, _out(result)
    assert (custom / "index.html").exists()
    assert not (project / "public" / "build" / "prerender" / "index.html").exists()


def test_app_flag_imports_alternate_target(tmp_path, monkeypatch):
    """--app module:attr boots a different callable than asgi:app."""
    project = _make_project(tmp_path, monkeypatch)
    (project / "alt_asgi.py").write_text(
        "from starlette.applications import Starlette\n"
        "from starlette.responses import PlainTextResponse\n"
        "from starlette.routing import Route\n"
        "PRERENDER_ROUTES = ['/alt']\n"
        "async def _alt(request):\n"
        "    return PlainTextResponse('ALT-APP', media_type='text/html')\n"
        "alt = Starlette(routes=[Route('/alt', _alt)])\n"
    )
    result = runner.invoke(cli_app, ["prerender", "--app", "alt_asgi:alt"])
    assert result.exit_code == 0, _out(result)
    html = (project / "public" / "build" / "prerender" / "alt" / "index.html").read_bytes()
    assert b"ALT-APP" in html


def test_app_flag_missing_attr_exits_one(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["prerender", "--app", "asgi:nope"])
    assert result.exit_code == 1
    assert "nope" in _out(result)


def test_out_flag_refuses_foreign_directory(tmp_path, monkeypatch):
    """--out pointed at a real directory must refuse, not clear it."""
    _make_project(tmp_path, monkeypatch)
    foreign = tmp_path / "elsewhere"
    foreign.mkdir()
    (foreign / "precious.txt").write_text("keep")
    result = runner.invoke(cli_app, ["prerender", "--out", str(foreign)])
    out = _out(result)
    assert result.exit_code == 2
    assert "--force" in out
    assert (foreign / "precious.txt").exists()
    assert not (foreign / "index.html").exists()


def test_force_flag_writes_foreign_directory(tmp_path, monkeypatch):
    """--force says "yes, clear it" and the run proceeds."""
    _make_project(tmp_path, monkeypatch)
    foreign = tmp_path / "elsewhere"
    foreign.mkdir()
    (foreign / "precious.txt").write_text("keep")
    result = runner.invoke(cli_app, ["prerender", "--out", str(foreign), "--force"])
    assert result.exit_code == 0, _out(result)
    assert (foreign / "index.html").exists()
    assert not (foreign / "precious.txt").exists()


def test_capture_failure_is_a_clean_exit_one(tmp_path, monkeypatch):
    """A capture-time crash must read as a one-line error, not a traceback.

    A failing app lifespan (dead DB, missing secret) raises inside
    asyncio.run; the command catches it, prints the cause in red, and
    exits 1 — same shape as the import-failure path.
    """
    _make_project(tmp_path, monkeypatch)
    (tmp_path / "asgi.py").write_text(
        "from contextlib import asynccontextmanager\n"
        "from starlette.applications import Starlette\n"
        "PRERENDER_ROUTES = ['/']\n"
        "@asynccontextmanager\n"
        "async def _boom(app):\n"
        "    raise RuntimeError('db unreachable during boot')\n"
        "    yield\n"
        "app = Starlette(routes=[], lifespan=_boom)\n"
    )
    result = runner.invoke(cli_app, ["prerender"])
    out = _out(result)
    assert result.exit_code == 1
    # typer.Exit arrives as SystemExit; anything else means a raw crash ran past.
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in out
    assert "prerender failed" in out
    assert "db unreachable during boot" in out


def test_timeout_flag_bounds_a_hanging_route(tmp_path, monkeypatch):
    """--timeout (seconds) caps each capture; a hung page fails fast.

    The route below sleeps far past the 0.1s budget — without the flag the
    command would sit there for the route's full sleep.
    """
    _make_project(tmp_path, monkeypatch)
    (tmp_path / "routes" / "web.py").write_text(
        "import asyncio\n"
        "from fastplace.http import Html, Router\n\n\n"
        "async def home(request):\n"
        "    await asyncio.sleep(30)\n"
        "    return Html('<h1>HOME-PAGE</h1>')\n\n\n"
        "async def gone(request):\n"
        "    return Html('<h1>gone</h1>', status_code=404)\n\n\n"
        "router = Router()\n"
        "router.get('/', home)\n"
        "router.get('/gone', gone)\n"
    )
    result = runner.invoke(cli_app, ["prerender", "--timeout", "0.1"])
    out = _out(result)
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "timed out" in out
    assert "/" in out  # the message names the route that hung


def test_second_run_recaptures_live_app_not_stale_output(tmp_path, monkeypatch):
    """Re-running the command must capture the live app, not the previous run's files.

    The CLI imports the real kernel app, which serves PrerenderStaticFiles
    from the very directory being refreshed — the capture request is exactly
    the interception shape (GET, text/html, no query). A capture-side bypass
    marker keeps the second run fresh.
    """
    project = _make_project(tmp_path, monkeypatch)
    assert runner.invoke(cli_app, ["prerender"]).exit_code == 0
    page = project / "public" / "build" / "prerender" / "index.html"
    assert b"HOME-PAGE" in page.read_bytes()

    # The live page changes between runs.
    (project / "routes" / "web.py").write_text(
        "from fastplace.http import Html, Router\n\n\n"
        "async def home(request):\n"
        "    return Html('<h1>HOME-PAGE-V2</h1>')\n\n\n"
        "async def gone(request):\n"
        "    return Html('<h1>gone</h1>', status_code=404)\n\n\n"
        "router = Router()\n"
        "router.get('/', home)\n"
        "router.get('/gone', gone)\n"
    )
    # Python must re-import the edited routes module (and the asgi module
    # that boots it) for the new run.
    import sys

    for name in ("routes.web", "routes", "asgi"):
        sys.modules.pop(name, None)

    result = runner.invoke(cli_app, ["prerender"])
    assert result.exit_code == 0, _out(result)
    assert b"HOME-PAGE-V2" in page.read_bytes()
