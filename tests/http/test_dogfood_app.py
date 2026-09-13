"""Dogfood app integration — the shipped routes/controllers/services stack."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

from fastplace.http import get_app

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _dogfood_app(**config):
    from routes.api import router as api_router
    from routes.web import router as web_router

    return get_app(routes=web_router, api_routes=api_router, config=config)


async def test_health_endpoint_via_routes_module():
    app = _dogfood_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "framework": "fastplace"}


async def test_dashboard_props_come_from_the_service_layer(monkeypatch):
    # Env vars always win in fastplace.config — this is the path the service
    # actually reads (get_app's config dict only wires the kernel itself).
    monkeypatch.setenv("APP_NAME", "Configured Name")
    app = _dogfood_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/", headers={"X-Fastplace-Request": "true"})
    body = resp.json()
    assert body["component"] == "Dashboard/Index"
    # appName flows from config through DashboardService — never hardcoded.
    assert body["props"]["appName"] == "Configured Name"
    assert body["props"]["projects"] == []


async def test_about_page_props_come_from_the_service_layer():
    app = _dogfood_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/about", headers={"X-Fastplace-Request": "true"})
    body = resp.json()
    assert body["component"] == "About/Index"
    assert body["props"]["framework"] == "fastplace"
    assert body["props"]["url"] == "/about"
