"""T7 — ``auth``/``guest`` route middleware + remember-cookie flush (spec §4.5)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from starlette.requests import Request as StarletteRequest

from fastplace.auth.guards import SessionGuard
from fastplace.auth.middleware import (
    CSRF_HEADER,
    AuthenticateMiddleware,
    CsrfMiddleware,
    GuestMiddleware,
    ResolveUserMiddleware,
)
from fastplace.auth.providers import dict_provider
from fastplace.auth.remember import REMEMBER_COOKIE_NAME, REMEMBER_COOKIE_TTL
from fastplace.http.kernel import get_app
from fastplace.http.request import Request
from fastplace.http.response import Redirect
from fastplace.http.router import Router

USER = SimpleNamespace(id=7, name="Firoz", email="firoz@example.test")
INTENDED_KEY = "url.intended"


def build_app(*, remember_login: bool = False):
    dict_provider.add(USER)
    guard = SessionGuard(dict_provider)

    class SecretController:
        async def show(self, request):
            return {"user": request.user.name if request.user else None}

    class LoginController:
        # The Task 9 pattern: login first, THEN redirect to intended() —
        # only the guard's carve-out keeps the parked URL alive across login.
        async def store(self, request):
            await guard.login(request, USER, remember=remember_login)
            return Redirect(request.intended(), status_code=303)

    class SilentLoginController:
        # Logs in WITHOUT consuming url.intended — stands in for "login
        # completed elsewhere", so GuestMiddleware's resume can be pinned
        # on its own (the normal login controller pops the parked URL).
        async def store(self, request):
            await guard.login(request, USER)
            return Redirect("/dashboard", status_code=303)

    class LogoutController:
        async def store(self, request):
            await guard.logout(request)
            return Redirect("/login-page", status_code=303)

    class LoginPageController:
        async def show(self, request):
            return {"page": "login"}

    router = Router()
    router.get("/protected", SecretController, "show", name="protected", middleware=["auth"])
    router.get(
        "/api/protected", SecretController, "show", name="api.protected", middleware=["auth"]
    )
    router.post("/login", LoginController, "store", name="auth.login.store")
    router.post("/silent-login", SilentLoginController, "store", name="auth.silent-login")
    router.post("/logout", LogoutController, "store", name="auth.logout")
    router.get("/login-page", LoginPageController, "show", name="auth.login", middleware=["guest"])
    return get_app(
        routes=router,
        middleware=[ResolveUserMiddleware(), CsrfMiddleware()],
        route_middleware={"auth": AuthenticateMiddleware, "guest": GuestMiddleware},
        config={"APP_ENV": "local", "APP_KEY": "route-protection-test-key"},
    )


@pytest.fixture()
async def client():
    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _csrf_token(client) -> str:
    # This app has no /me, so bootstrap against /login-page (same pattern
    # as tests/auth/conftest.bootstrap_csrf).
    page = await client.get("/login-page")
    return page.headers["X-Fastplace-CSRF-Token"]


async def _login(client) -> httpx.Response:
    token = await _csrf_token(client)
    return await client.post("/login", headers={CSRF_HEADER: token})


def _remember_set_cookie(headers) -> str:
    lines = [h for h in headers.get_list("set-cookie") if h.startswith(f"{REMEMBER_COOKIE_NAME}=")]
    assert lines, f"no {REMEMBER_COOKIE_NAME} Set-Cookie on the response"
    return lines[0]


class TestAuthenticateMiddleware:
    async def test_unauthenticated_browser_get_redirects_to_login(self, client):
        response = await client.get("/protected")
        assert response.status_code == 302
        assert response.headers["location"] == "/login"

    async def test_unauthenticated_bridge_get_returns_401_envelope(self, client):
        response = await client.get("/protected", headers={"X-Fastplace-Request": "true"})
        assert response.status_code == 401
        assert response.json() == {"message": "Unauthenticated."}

    async def test_unauthenticated_api_path_get_returns_401_envelope(self, client):
        response = await client.get("/api/protected", headers={"Accept": "application/json"})
        assert response.status_code == 401
        assert response.json() == {"message": "Unauthenticated."}

    async def test_parks_the_intended_url_and_login_resumes_it(self, client):
        # 1. Anonymous hit on a deep link parks the full path (query kept).
        parked = await client.get("/protected", params={"tab": 2})
        assert parked.status_code == 302

        # 2. Login redirects to the parked URL, not the dashboard default.
        token = await _csrf_token(client)
        response = await client.post("/login", headers={CSRF_HEADER: token})
        assert response.status_code == 303
        assert response.headers["location"] == "/protected?tab=2"

        # 3. The parked URL is consumed once — a later guest hit falls back.
        again = await client.get("/login-page")
        assert again.status_code == 302
        assert again.headers["location"] == "/dashboard"

    async def test_authenticated_request_passes_through(self, client):
        await _login(client)
        response = await client.get("/protected")
        assert response.status_code == 200
        assert response.json() == {"user": "Firoz"}


class TestGuestMiddleware:
    async def test_authenticated_user_bounces_to_dashboard(self, client):
        await _login(client)
        response = await client.get("/login-page")
        assert response.status_code == 302
        assert response.headers["location"] == "/dashboard"

    async def test_guest_resumes_the_intended_url_after_login(self, client):
        # Park the deep link anonymously, then log in WITHOUT consuming it.
        await client.get("/protected")
        token = await _csrf_token(client)
        await client.post("/silent-login", headers={CSRF_HEADER: token})

        response = await client.get("/login-page")
        assert response.status_code == 302
        assert response.headers["location"] == "/protected"

    async def test_anonymous_guest_passes_through(self, client):
        response = await client.get("/login-page")
        assert response.status_code == 200
        assert response.json() == {"page": "login"}


class FakeRememberStore:
    """Dict-backed remember store — DB-free flush tests."""

    def __init__(self) -> None:
        self.rows: dict[int, list[str]] = {}
        self._next_id = 100

    async def issue(self, user_id) -> str:
        self._next_id += 1
        validator = f"v{self._next_id}"
        self.rows.setdefault(user_id, []).append(validator)
        return f"{self._next_id}|{validator}"

    async def consume(self, cookie):
        _, _, validator = cookie.partition("|")
        for user_id, validators in self.rows.items():
            if validator in validators:
                validators.remove(validator)
                return user_id, await self.issue(user_id)
        return None

    async def revoke(self, cookie) -> None:
        _, _, validator = cookie.partition("|")
        for validators in self.rows.values():
            if validator in validators:
                validators.remove(validator)
                return

    async def revoke_all_for_user(self, user_id) -> int:
        return len(self.rows.pop(user_id, []))


class TestRememberCookieFlush:
    async def test_login_with_remember_sets_and_rotates_the_cookie(self, monkeypatch):
        import fastplace.auth.guards as guards_module

        fake = FakeRememberStore()
        monkeypatch.setattr(guards_module, "remember_store", lambda: fake)

        app = build_app(remember_login=True)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            token = await _csrf_token(client)
            response = await client.post("/login", headers={CSRF_HEADER: token})
            assert response.status_code == 303

            raw = _remember_set_cookie(response.headers)
            assert f"Max-Age={REMEMBER_COOKIE_TTL}" in raw
            value = raw.split(";", 1)[0].split("=", 1)[1]
            assert "|" in value

            # A bare client carries ONLY the remember cookie (empty session):
            # the fallback must resolve the user and queue a rotated cookie.
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as bare:
                resumed = await bare.get(
                    "/protected", headers={"Cookie": f"{REMEMBER_COOKIE_NAME}={value}"}
                )
            assert resumed.status_code == 200
            assert resumed.json() == {"user": "Firoz"}

            rotated_raw = _remember_set_cookie(resumed.headers)
            rotated = rotated_raw.split(";", 1)[0].split("=", 1)[1]
            assert rotated != value, "the remembered pair must rotate on use"

    async def test_logout_clears_the_remember_cookie_header(self, monkeypatch):
        import fastplace.auth.guards as guards_module

        fake = FakeRememberStore()
        monkeypatch.setattr(guards_module, "remember_store", lambda: fake)

        app = build_app(remember_login=True)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            token = await _csrf_token(client)
            login = await client.post("/login", headers={CSRF_HEADER: token})
            assert login.status_code == 303
            assert client.cookies.get(REMEMBER_COOKIE_NAME)

            # login rotated the CSRF token — use the fresh one for logout.
            fresh_token = login.headers["X-Fastplace-CSRF-Token"]
            response = await client.post("/logout", headers={CSRF_HEADER: fresh_token})
            assert response.status_code == 303

            clear = _remember_set_cookie(response.headers)
            assert "Max-Age=0" in clear
            assert fake.rows[7] == [], "the remember pair must be revoked"


class TestRequestIntended:
    def test_request_intended_pops_the_parked_url(self):
        scope: dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "session": {INTENDED_KEY: "/settings/security"},
        }
        request = Request(StarletteRequest(scope))

        assert request.intended() == "/settings/security"
        assert request.intended() == "/dashboard"  # popped — default after

    def test_request_intended_defaults_without_a_parked_url(self):
        scope: dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "session": {},
        }
        request = Request(StarletteRequest(scope))

        assert request.intended() == "/dashboard"
        assert request.intended(default="/home") == "/home"
