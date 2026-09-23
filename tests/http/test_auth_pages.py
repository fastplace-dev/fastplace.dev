"""Guest auth pages — GET-only bridge renders behind the guest middleware."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from fastplace.http import get_app

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


EMPTY_PROPS_AUTH_PAGES = [
    ("/login", "Auth/Login"),
    ("/forgot-password", "Auth/ForgotPassword"),
    ("/verify-email", "Auth/VerifyEmail"),
    ("/user/confirm-password", "Auth/ConfirmPassword"),
    ("/two-factor-challenge", "Auth/TwoFactorChallenge"),
]


def _sample_app(**config):
    from routes.api import router as api_router
    from routes.web import router as web_router

    return get_app(routes=web_router, api_routes=api_router, config=config)


@pytest.mark.parametrize(("path", "component"), EMPTY_PROPS_AUTH_PAGES)
async def test_auth_page_serves_the_ported_component(path, component):
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # Bridge request — must answer with the auth page payload.
        resp = await c.get(path, headers={"X-Fastplace-Request": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["component"] == component
    # Auth pages read optional props with typed defaults — none required yet.
    assert body["props"] == {}


async def test_register_page_serves_the_ported_component_with_password_rules():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/register", headers={"X-Fastplace-Request": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["component"] == "Auth/Register"
    # The register page's client-side default is "minlength: 8;" — the server
    # prop carries the same configured policy that validates the POST.
    assert body["props"] == {"passwordRules": "minlength: 8;"}


async def test_reset_password_forwards_query_string_to_page_props():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(
            "/reset-password",
            params={"token": "abc", "email": "user@example.test"},
            headers={"X-Fastplace-Request": "true"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["component"] == "Auth/ResetPassword"
    # The page reads token/email from props, so the email link's query
    # string must be carried into the payload.
    assert body["props"] == {"token": "abc", "email": "user@example.test"}


async def test_reset_password_defaults_missing_query_string_to_empty_strings():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/reset-password", headers={"X-Fastplace-Request": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["component"] == "Auth/ResetPassword"
    assert body["props"] == {"token": "", "email": ""}


async def test_login_full_document_load():
    app = _sample_app(APP_ENV="local")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/login")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
