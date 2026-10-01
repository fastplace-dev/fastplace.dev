"""serve rebuilds the prerender tree its Vite build just wiped.

`fastplace serve` runs `npm run build` first, and the scaffold's Vite
config empties public/build wholesale (emptyOutDir) — taking
public/build/prerender with it. Contract: snapshot the prerender manifest
before the build; after a successful build, recapture in-process (the
same fastplace.prerender path the CLI uses) when the app ships prerendered
(a pre-build manifest) or configures routes explicitly (asgi
PRERENDER_ROUTES attr or PRERENDER_ROUTES env). A never-prerendered app
with no explicit routes must NOT start prerendering implicitly, and a
failed recapture fails serve loudly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from tests.cli._isolation import isolate_project_state  # noqa: F401

runner = CliRunner()

_HOME_ROUTE = (
    "from fastplace.http import Html, Router\n"
    "\n"
    "\n"
    "async def home(request):\n"
    "    return Html('<h1>HOME-PAGE</h1>')\n"
    "\n"
    "\n"
    "async def gone(request):\n"
    "    return Html('<h1>gone</h1>', status_code=404)\n"
    "\n"
    "\n"
    "router = Router()\n"
    "router.get('/', home)\n"
    "router.get('/gone', gone)\n"
)


def _make_serve_project(spawned, *, routes_body: str = "") -> None:
    """A real kernel app inside the spawned tmp project (fake Popen children)."""
    root = spawned.root
    (root / "asgi.py").write_text(
        f"from fastplace.http import create_app\napp = create_app()\n{routes_body}"
    )
    (root / "config" / "app.py").write_text(
        'APP_ENV = "local"\nAPP_KEY = "k" * 48\nAPP_URL = "http://fastplace.local"\n'
    )
    (root / "routes").mkdir(exist_ok=True)
    (root / "routes" / "__init__.py").write_text("")
    (root / "routes" / "web.py").write_text(_HOME_ROUTE)
    (root / ".env").write_text("APP_ENV=local\nAPP_KEY=" + "k" * 48 + "\n")
    (root / "package.json").write_text("{}\n")


@pytest.fixture()
def wiping_build(spawned, monkeypatch):
    """Fake npm on PATH + a `npm run build` that empties public/build —
    exactly what Vite's emptyOutDir does — then reports success."""
    monkeypatch.delenv("PRERENDER_ROUTES", raising=False)
    monkeypatch.setattr(
        "shutil.which", lambda name: "/fakebin/npm" if name == "npm" else None, raising=False
    )
    real_run = subprocess.run
    builds: list[list[str]] = []

    def fake_run(command, *args, **kwargs):  # noqa: ANN002, ANN003
        # Match on the argv shape, not "npm in command": command[0] is the
        # resolved path (/fakebin/npm), and a missed match would delegate to
        # the real subprocess.run — whose `with Popen(...)` lands on the
        # spawned fixture's fake and explodes as a context-manager TypeError.
        if len(command) >= 3 and command[1:3] == ["run", "build"]:
            builds.append(list(command))
            build_dir = spawned.root / "public" / "build"
            if build_dir.exists():
                shutil.rmtree(build_dir)
            return SimpleNamespace(returncode=0)
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr("fastplace.cli.dev.subprocess.run", fake_run)
    return builds


@pytest.fixture(autouse=True)
def _uncache_project_modules():
    """The recapture imports asgi/routes in-process; drop them after each
    test so the next tmp project is not shadowed by a cached one."""
    yield
    for name in ("routes.web", "routes", "asgi"):
        sys.modules.pop(name, None)


def _prerender_dir(spawned):
    return spawned.root / "public" / "build" / "prerender"


def test_planted_prerender_tree_survives_the_build(spawned, wiping_build):
    """A previously prerendered app: the build wipes the tree, serve must
    recapture it (fresh from the live app) before booting uvicorn."""
    _make_serve_project(spawned)
    prerender = _prerender_dir(spawned)
    prerender.mkdir(parents=True)
    (prerender / "index.html").write_bytes(b"<h1>STALE</h1>")
    (prerender / "prerender-manifest.json").write_text('{"routes": ["/"]}\n')

    result = runner.invoke(cli_app, ["serve", "--workers", "1"])

    assert result.exit_code == 0, result.output
    assert wiping_build, "the Vite build never ran"
    assert (prerender / "prerender-manifest.json").is_file()
    assert b"HOME-PAGE" in (prerender / "index.html").read_bytes()  # live, not stale
    assert any("uvicorn" in cmd for cmd in spawned.commands)


def test_explicit_routes_recapture_even_without_a_manifest(spawned, wiping_build):
    """A fresh deploy that configured PRERENDER_ROUTES but never ran the
    prerender CLI still ships a prerendered tree after serve."""
    _make_serve_project(spawned, routes_body="PRERENDER_ROUTES = ['/']\n")

    result = runner.invoke(cli_app, ["serve", "--workers", "1"])

    assert result.exit_code == 0, result.output
    prerender = _prerender_dir(spawned)
    assert (prerender / "prerender-manifest.json").is_file()
    manifest = json.loads((prerender / "prerender-manifest.json").read_text())
    assert manifest["routes"] == ["/"]
    assert b"HOME-PAGE" in (prerender / "index.html").read_bytes()


def test_plain_app_never_prerenders_implicitly(spawned, wiping_build):
    """No manifest, no explicit routes: serve must not invent a prerender
    step for an app that never opted in."""
    _make_serve_project(spawned)

    result = runner.invoke(cli_app, ["serve", "--workers", "1"])

    assert result.exit_code == 0, result.output
    assert not _prerender_dir(spawned).exists()
    assert any("uvicorn" in cmd for cmd in spawned.commands)


def test_failed_recapture_fails_serve_loudly(spawned, wiping_build):
    """The tree is part of the deploy: a capture that fails after a wiping
    build must exit non-zero with a one-line red message, not boot uvicorn
    serving a half-built artifact."""
    _make_serve_project(spawned, routes_body="PRERENDER_ROUTES = ['/']\n")
    (spawned.root / "asgi.py").write_text(
        "from contextlib import asynccontextmanager\n"
        "from starlette.applications import Starlette\n"
        "PRERENDER_ROUTES = ['/']\n"
        "@asynccontextmanager\n"
        "async def _boom(app):\n"
        "    raise RuntimeError('db unreachable during boot')\n"
        "    yield\n"
        "app = Starlette(routes=[], lifespan=_boom)\n"
    )

    result = runner.invoke(cli_app, ["serve", "--workers", "1"])

    assert result.exit_code == 1
    # Collapse whitespace: Rich hard-wraps the one-line message at the
    # runner's console width, mid-sentence.
    flat = " ".join(result.output.split())
    assert "prerender" in flat
    assert "db unreachable during boot" in flat
    assert not any("uvicorn" in cmd for cmd in spawned.commands)
