"""Account settings pages — GET-only bridge renders until the Phase 4 auth backend.

Profile and Security are fully ported pages the settings section nav already
links to; these routes make those links resolve. Form targets (profile update,
password update, passkeys, two-factor) stay unrouted until the auth phase.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

from fastplace.http import get_app

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _sample_app(**config):
    from routes.api import router as api_router
    from routes.web import router as web_router

    return get_app(routes=web_router, api_routes=api_router, config=config)


async def test_settings_profile_serves_the_ported_component():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/settings/profile", headers={"X-Fastplace-Request": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["component"] == "Settings/Profile"
    # The page reads auth?.user as optional and degrades to empty fields —
    # no server props until the auth backend lands.
    assert body["props"] == {}


async def test_settings_security_serves_the_ported_component_with_password_rules():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/settings/security", headers={"X-Fastplace-Request": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["component"] == "Settings/Security"
    # The page has no client-side default for passwordRules (register does),
    # so the server supplies the same default the register page applies.
    assert body["props"] == {"passwordRules": "minlength: 8;"}


async def test_settings_pages_full_document_load():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        for path in ("/settings/profile", "/settings/security"):
            resp = await c.get(path)
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"]
