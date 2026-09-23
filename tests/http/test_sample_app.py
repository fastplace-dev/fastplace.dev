"""Sample app integration — the shipped routes/controllers/services stack."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

from fastplace.http import get_app
from fastplace.http.middleware import Middleware

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class _AuthBypassed(Middleware):
    """Rendering-only stand-in for the ``auth`` route middleware.

    The settings pages sit behind ``auth`` now; their rendering payloads are
    pinned here without the session/DB stack, while the protection contract
    (park/resume/401) lives in tests/sample/test_auth_endpoints.py.
    """

    async def handle(self, request, call_next):
        return await call_next(request)


def _sample_app(**config):
    from routes.api import router as api_router
    from routes.web import router as web_router

    return get_app(
        routes=web_router,
        api_routes=api_router,
        route_middleware={"auth": _AuthBypassed},
        config=config,
    )


async def test_health_endpoint_via_routes_module():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "framework": "fastplace"}


async def test_dashboard_props_come_from_the_service_layer(monkeypatch, tmp_path):
    # Env vars always win in fastplace.config — this is the path the service
    # actually reads (get_app's config dict only wires the kernel itself).
    monkeypatch.setenv("APP_NAME", "Configured Name")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/sample_app.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")

    from app.modules.knowledge.models.knowledge_item import KnowledgeItem  # noqa: F401
    from app.modules.projects.models.project import Project  # noqa: F401
    from app.modules.projects.models.task import Task  # noqa: F401
    from fastplace.db import db, reset_db

    reset_db()
    await db.create_all()

    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/dashboard", headers={"X-Fastplace-Request": "true"})
    body = resp.json()
    assert body["component"] == "Dashboard/Index"
    # appName flows from config through DashboardService — never hardcoded.
    assert body["props"]["appName"] == "Configured Name"
    assert body["props"]["recent_projects"] == []
    assert body["props"]["stats"] == {"projects": 0, "open_tasks": 0, "completed_tasks": 0}
    assert body["props"]["knowledge_items"] == 0


async def test_about_page_props_come_from_the_service_layer():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/about", headers={"X-Fastplace-Request": "true"})
    body = resp.json()
    assert body["component"] == "About/Index"
    assert body["props"]["framework"] == "fastplace"
    assert body["props"]["url"] == "/about"


async def test_settings_appearance_page_serves_the_ported_component():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # Bridge request — must answer with the Settings/Appearance payload.
        resp = await c.get("/settings/appearance", headers={"X-Fastplace-Request": "true"})
    body = resp.json()
    assert body["component"] == "Settings/Appearance"
    # The appearance page is fully client-side — no server props required.
    assert body["props"] == {}


async def test_settings_appearance_full_document_load():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/settings/appearance")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


async def test_home_page_serves_the_ported_component():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/", headers={"X-Fastplace-Request": "true"})
    body = resp.json()
    assert body["component"] == "Home/Index"
    # The home page reads only optional shared data — no server props.
    assert body["props"] == {}


async def test_home_page_full_document_load():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
