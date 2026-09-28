"""Kernel hooks — every app carries request correlation; boot configures logs.

create_app() is the real boot path (dev via dev_shell, serve via asgi.py);
get_app() is the test-factory path. The middleware belongs to every app, the
logging bootstrap only to real boots — the same split the shared-props hook
documents in the kernel.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import fastplace.logging
from fastplace.http.kernel import create_app, get_app
from fastplace.logging.middleware import RequestIdMiddleware


def test_get_app_installs_request_id_middleware_outermost():
    app = get_app()
    # add_middleware inserts at index 0 — the last-added is user_middleware[0].
    assert app.user_middleware[0].cls is RequestIdMiddleware


def test_get_app_responses_carry_request_id():
    from fastplace.http import Router

    router = Router()

    async def ping(request):
        return {"pong": True}

    router.get("/ping", ping)
    app = get_app(routes=router)
    with TestClient(app) as client:
        response = client.get("/ping")
    assert response.headers["x-request-id"]


@pytest.fixture()
def minimal_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A bootable project — same shape the CLI test suite boots."""
    (tmp_path / "asgi.py").write_text("from fastplace.http import create_app\napp = create_app()\n")
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
    (tmp_path / ".env").write_text("APP_ENV=local\n")
    monkeypatch.chdir(tmp_path)
    # create_app boots leak by design: .env keys join os.environ, the
    # project's routes modules join sys.modules, and the project root joins
    # sys.path. Restore all three so later suites see the process as it was.
    env_before = set(os.environ)
    path_before = list(sys.path)
    yield tmp_path
    for key in set(os.environ) - env_before:
        os.environ.pop(key, None)
    sys.path[:] = path_before
    for name in ("routes", "routes.web", "routes.auth", "routes.api", "routes.ai"):
        module = sys.modules.get(name)
        if module is not None:
            file = getattr(module, "__file__", None)
            if file and Path(file).is_relative_to(tmp_path):
                del sys.modules[name]


def test_create_app_configures_logging_with_project_root(
    minimal_project: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[dict] = []
    real = fastplace.logging.configure_logging

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(fastplace.logging, "configure_logging", spy)
    app = create_app()
    assert len(calls) == 1
    assert calls[0]["root"] == minimal_project
    assert app.user_middleware[0].cls is RequestIdMiddleware
    # The default single channel is live immediately after boot.
    ours = [
        h
        for h in __import__("logging").getLogger().handlers
        if getattr(h, "fastplace_logging", False)
    ]
    assert len(ours) == 1


def test_create_app_boot_is_idempotent_on_second_call(
    minimal_project: Path, monkeypatch: pytest.MonkeyPatch
):
    """A second create_app in the same process must not stack handlers."""
    import logging

    create_app()
    handlers_after_first = [
        h for h in logging.getLogger().handlers if getattr(h, "fastplace_logging", False)
    ]
    count_after_first = len(handlers_after_first)
    create_app()
    handlers_after_second = [
        h for h in logging.getLogger().handlers if getattr(h, "fastplace_logging", False)
    ]
    assert len(handlers_after_second) == count_after_first


def test_unhandled_500_logs_traceback_with_request_id(capture_records):
    """A production 500 keeps a generic body but traces into the log."""
    from fastplace.http import Router, get_app

    router = Router()

    async def boom(request):
        raise RuntimeError("kaboom")

    router.get("/boom", boom)
    app = get_app(routes=router)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom", headers={"X-Request-ID": "err-trace-1"})
    assert response.status_code == 500
    joined = "\n".join(capture_records.lines)
    assert "unhandled exception during GET /boom" in joined
    assert "kaboom" in joined  # traceback rides the ERROR record
    assert "request_id=err-trace-1" in joined
