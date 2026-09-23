"""Shared fixtures for the auth test-suite: a sample-app-style guarded app."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from fastplace.auth.providers import dict_provider
from fastplace.http.kernel import get_app
from fastplace.http.router import Router

TEST_USER = SimpleNamespace(id=7, name="Firoz", email="firoz@example.test")


@pytest.fixture(autouse=True)
def _dict_provider_for_default_guard(monkeypatch: pytest.MonkeyPatch):
    """Pin guard()'s config-driven provider to the dict singleton.

    config/auth.py ships the ORM provider for the sample app; this suite
    exercises the framework middleware against SimpleNamespace users, and
    ResolveUserMiddleware builds its guard through the global config — the
    provider it resolves must agree with the SessionGuard(dict_provider)
    the test apps construct explicitly, or logins and resolutions diverge.
    """
    monkeypatch.setattr(
        "fastplace.auth.guards.provider_from_config", lambda config_get: dict_provider
    )


def build_auth_app() -> object:
    """App with session login/logout, a guarded ``/me`` echo, and a POST
    endpoint behind CSRF — mirrors the sample app's middleware stack."""
    from fastplace.auth.guards import SessionGuard
    from fastplace.auth.middleware import CsrfMiddleware, ResolveUserMiddleware
    from fastplace.auth.providers import dict_provider

    # One provider for the whole app — the same singleton the middleware's
    # default guard resolves through, so logins and resolutions agree.
    dict_provider.add(TEST_USER)
    session = SessionGuard(dict_provider)

    class AuthController:
        async def login(self, request):
            await session.login(request, TEST_USER)
            return {"ok": True, "user": TEST_USER.name}

        async def logout(self, request):
            await session.logout(request)
            return {"ok": True}

    router = Router()
    router.post("/login", AuthController, "login", name="auth.login")
    router.post("/logout", AuthController, "logout", name="auth.logout")

    class MeController:
        async def index(self, request):
            user = request.user
            return {"user": getattr(user, "name", None)}

    class SubmitController:
        async def store(self, request):
            # Echo the parsed form/JSON payload back — the CSRF middleware must
            # not drain the body before the controller reads it.
            content_type = (request.header("Content-Type") or "").lower()
            if "json" in content_type:
                body = await request.json()
            else:
                form = await request.form()
                body = {key: form.get(key) for key in form.keys()}
            return {"ok": True, "payload": (body or {}).get("payload")}

    class WebhookController:
        async def store(self, request):
            return {"received": True}

    class PageController:
        async def show(self, request):
            from fastplace.http import render

            return render(request, component="Dashboard/Index", props={"ok": True})

    router.get("/me", MeController, "index", name="auth.me")
    router.post("/submit", SubmitController, "store", name="submit")
    router.post("/webhook/order", WebhookController, "store", name="webhook.order")
    router.get("/page", PageController, "show", name="page.show")

    return get_app(
        routes=router,
        middleware=[ResolveUserMiddleware(), CsrfMiddleware()],
        config={"APP_ENV": "local", "APP_KEY": "test-app-key-not-for-production-use-only"},
    )


@pytest.fixture()
async def auth_client():
    transport = httpx.ASGITransport(app=build_auth_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def bootstrap_csrf(client) -> str:
    """GET any page first (issues the session token), then return it."""
    page = await client.get("/me")
    return page.headers["X-Fastplace-CSRF-Token"]


@pytest.fixture()
def registered_user():
    """One user pre-registered on the framework's dict provider singleton."""
    dict_provider.add(TEST_USER)
    return TEST_USER
