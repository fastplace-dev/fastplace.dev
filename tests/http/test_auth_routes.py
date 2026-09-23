"""T9 — auth POST endpoints + kernel auth_routes discovery (spec §4.5)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.http.controllers.auth_api_controller import AuthApiController
from fastplace.http.kernel import get_app
from fastplace.http.router import Router


def auth_router() -> Router:
    """Middleware-free mount of the credential endpoints.

    The real ``routes/auth.py`` declares the ``guest``/``throttle``/``auth``
    aliases; this router mounts the same controller without aliases so the
    endpoint contract is testable independently of the registry (the boot
    test below pins that the aliases themselves resolve).
    """
    router = Router()
    router.post("/login", AuthApiController, "login", name="auth.login.store")
    router.post("/register", AuthApiController, "register", name="auth.register.store")
    router.post("/logout", AuthApiController, "logout", name="auth.logout")
    return router


@pytest.fixture()
async def client():
    # auth_routes alone: if the kernel ignored the parameter, every endpoint
    # below would 404 — the fixture itself pins the mounting contract.
    app = get_app(
        auth_routes=auth_router(),
        config={"APP_ENV": "local", "APP_KEY": "auth-routes-test-key"},
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestValidation:
    async def test_login_with_an_empty_body_returns_422_field_errors(self, client):
        response = await client.post("/login", json={})
        assert response.status_code == 422
        body = response.json()
        assert "errors" in body and "email" in body["errors"]

    async def test_register_with_an_empty_body_returns_422(self, client):
        response = await client.post("/register", json={})
        assert response.status_code == 422
        assert "errors" in response.json()


class TestRoutes:
    async def test_logout_without_a_session_redirects_to_login(self, client):
        response = await client.post("/logout", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


class TestKernelDiscovery:
    def test_create_app_mounts_the_auth_router_module(self):
        # create_app discovers routes/auth.py the same way it does
        # routes/web.py — unit-level: the loader resolves the module and
        # exposes its routes.
        from fastplace.http.kernel import _load_router_module

        router = _load_router_module(Path.cwd(), "routes.auth")
        assert router is not None
        paths = {route.path for route in router.routes}
        assert "/login" in paths and "/register" in paths and "/logout" in paths
        # Credential endpoints are POSTs; the §4.11 verification fulfill link
        # is the router's one GET outside the two-factor management surface —
        # §4.13's /user/two-factor* routes carry their own frozen methods
        # (GETs for qr/secret/codes fetches, one DELETE to disable).
        assert all(
            route.method == "POST"
            for route in router.routes
            if route.path != "/email/verify/{id}/{hash}"
            and not route.path.startswith("/user/two-factor")
            and not route.path.startswith("/api/tokens")
        )
        assert any(
            route.method == "GET" and route.path == "/email/verify/{id}/{hash}"
            for route in router.routes
        )
        # Personal access tokens (spec §4.19) — literal /api paths on the
        # root mount; the DELETE revoke is the router's first non-POST API
        # verb alongside the two-factor management GETs/DELETE.
        assert "/api/tokens" in paths and "/api/token" in paths
        assert any(
            route.method == "DELETE" and route.path == "/api/tokens/{id}" for route in router.routes
        )

    def test_create_app_boots_and_resolves_the_declared_aliases(self):
        # The real boot path (asgi.py, `run dev`, `serve`) resolves every
        # alias routes/auth.py declares against the app's ROUTE_MIDDLEWARE at
        # mount time — an unregistered alias kills the boot, so the registry
        # entries and the routes that name them must ship together.
        import os

        from fastplace.http.kernel import create_app

        root = Path(__file__).resolve().parents[2]
        env_before = set(os.environ)
        try:
            app = create_app(root)
        finally:
            # create_app loads the repo .env into the process environment
            # (never overriding) — leave the suite's environment as it was.
            for key in set(os.environ) - env_before:
                os.environ.pop(key, None)

        # Reverse lookup by route name — agnostic to how the router nests
        # inside app.routes, and it only resolves when the route mounted.
        assert app.url_path_for("auth.login.store") == "/login"
        assert app.url_path_for("auth.register.store") == "/register"
        assert app.url_path_for("auth.logout") == "/logout"
        assert app.url_path_for("auth.tokens.store") == "/api/tokens"
        assert app.url_path_for("auth.tokens.destroy", id="1") == "/api/tokens/1"
        assert app.url_path_for("auth.token.mobile") == "/api/token"
