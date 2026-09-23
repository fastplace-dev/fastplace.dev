"""Account settings pages — authenticated GET renders the ported components.

Profile and Security are fully ported pages the settings section nav links
to; the routes sit behind the ``auth`` route middleware (the full protection
contract — park, resume, 401 bridge envelope — is pinned end-to-end in
tests/sample/test_auth_endpoints.py). These tests pin the rendering payload,
so the app below substitutes the ``auth`` alias with a pass-through instead
of standing up the whole session/DB stack. Form targets (profile update,
password update, passkeys, two-factor) stay unrouted until later phases.
"""

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
    """Rendering-only stand-in for the ``auth`` route middleware."""

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


async def test_settings_profile_serves_the_ported_component():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/settings/profile", headers={"X-Fastplace-Request": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["component"] == "Settings/Profile"
    # The page reads auth?.user as optional and degrades to empty fields —
    # the shared auth.user prop arrives with the later auth phases.
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
    # so the server supplies the configured policy in the frontend dialect.
    # The two-factor props drive the ManageTwoFactor card (R9) — auth is
    # bypassed here, so no user is attached and twoFactorEnabled is False.
    assert body["props"] == {
        "passwordRules": "minlength: 8;",
        "canManageTwoFactor": True,
        "requiresConfirmation": True,
        "twoFactorEnabled": False,
    }


async def test_settings_pages_full_document_load():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        for path in ("/settings/profile", "/settings/security"):
            resp = await c.get(path)
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"]
