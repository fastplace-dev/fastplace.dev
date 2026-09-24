"""``fastplace route:list`` — the project's route inventory (spec #3)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Rich colorizes when the environment forces color; strip codes before matching.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()

# Layered onto the scaffold's routes/api.py so the table carries a POST row
# (method filtering) and a websocket row (the "WS" representation) next to
# the scaffolded GET / home route.
_API_ROUTES_FIXTURE = '''"""API routes — fixture surface for route:list tests."""

from __future__ import annotations

from fastplace.http import Json, Router

router = Router()


async def echo(request):
    return Json({"ok": True})


async def pinger(ws):
    await ws.accept()
    await ws.send_text("pong")
    await ws.close()


router.post("/echo", echo, name="api.echo")
router.websocket("/ws", pinger, name="api.ws")
'''


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A scaffolded project cwd carrying GET, POST, and WS routes.

    ``route:list`` boots ``asgi:app`` in-process, which mutates process
    state beyond the module cache ``collect_routes`` already restores
    (.env vars, lifecycle hooks, shared props, the default config
    registry) — snapshot and restore all of it so later tests in the
    same process see none of this project.
    """
    from fastplace.http import lifecycle
    from fastplace.http.render import reset_shared_props

    cwd_before = Path.cwd().resolve()
    env_before = dict(os.environ)
    startup_before = list(lifecycle._startup_hooks)
    shutdown_before = list(lifecycle._shutdown_hooks)

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["new", "blog"])
    assert result.exit_code == 0, result.output

    root = tmp_path / "blog"
    (root / "routes" / "api.py").write_text(_API_ROUTES_FIXTURE)
    # Under pytest the framework checkout sits on sys.path (pythonpath =
    # ["."]), and its sample ``app/`` is a regular package — which would beat
    # the scaffold's namespace ``app/`` regardless of path order and boot the
    # checkout's jobs/gates instead of this project's. A regular package at
    # the front of sys.path wins; same workaround as test_make_agent.
    (root / "app" / "__init__.py").write_text("")
    monkeypatch.chdir(root)

    reset_shared_props()
    try:
        yield root
    finally:
        reset_shared_props()
        lifecycle._startup_hooks[:] = startup_before
        lifecycle._shutdown_hooks[:] = shutdown_before
        os.environ.clear()
        os.environ.update(env_before)
        from fastplace.config import reset_config

        reset_config(cwd_before)


def _run(*args):
    result = runner.invoke(cli_app, ["route:list", *args])
    return result.exit_code, ANSI_RE.sub("", result.output)


def test_lists_web_api_and_websocket_routes(project):
    code, out = _run()
    assert code == 0, out
    assert "home" in out  # the scaffolded GET / route, by name
    assert "/api/v1/echo" in out  # API routes carry their mount prefix
    assert "api.echo" in out
    assert "POST" in out
    assert "/api/v1/ws" in out and "WS" in out  # websockets report as WS


def test_method_filter_drops_non_get_rows(project):
    code, out = _run("--method", "GET")
    assert code == 0, out
    assert "home" in out
    assert "/api/v1/echo" not in out
    assert "/api/v1/ws" not in out


def test_method_filter_is_case_insensitive(project):
    code, out = _run("--method", "post")
    assert code == 0, out
    assert "/api/v1/echo" in out
    assert "home" not in out


def test_path_filter_is_a_substring_match(project):
    code, out = _run("--path", "echo")
    assert code == 0, out
    assert "/api/v1/echo" in out
    assert "home" not in out
    assert "/api/v1/ws" not in out


def test_name_filter_is_a_substring_match(project):
    code, out = _run("--name", "api.ws")
    assert code == 0, out
    assert "/api/v1/ws" in out
    assert "home" not in out
    assert "/api/v1/echo" not in out


def test_collect_routes_returns_method_path_name_dicts(project):
    from fastplace.cli.inspect import collect_routes

    rows = collect_routes()
    assert all(set(row) == {"method", "path", "name"} for row in rows)
    echo = next(row for row in rows if row["path"] == "/api/v1/echo")
    assert echo["method"] == "POST"
    assert echo["name"] == "api.echo"
    ws = next(row for row in rows if row["path"] == "/api/v1/ws")
    assert ws["method"] == "WS"


def test_outside_a_project_fails_friendly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["route:list"])
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in ANSI_RE.sub("", result.output)


def test_project_boot_leaves_no_cached_project_modules(project):
    """The in-process boot must not leave this project's modules cached —
    a later ``import routes``/``import app`` elsewhere in the process would
    silently resolve to this throwaway fixture project."""
    code, _ = _run()
    assert code == 0
    leaked = [
        name
        for name, module in sys.modules.items()
        if name.split(".", 1)[0] in ("asgi", "routes", "app")
        and str(project.resolve()) in str(getattr(module, "__file__", "") or "")
    ]
    assert leaked == []
