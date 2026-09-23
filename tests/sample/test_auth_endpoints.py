"""T10 — credential endpoints end-to-end through the sample app (spec §4.5).

The full shipped stack: routes/auth.py mounted beside routes/web.py, the
config-driven ORM user provider, route middleware aliases resolved from
config/app.py, and the CSRF/session middleware from the global stack.
"""

from __future__ import annotations

import re

import httpx
import pytest

REMEMBER_COOKIE = re.compile(r"fastplace_remember=[^;=\s]+\|[^;=\s]+")

REGISTER_PAYLOAD = {
    "name": "Firoz",
    "email": "firoz@example.test",
    "password": "secret123",
    "password_confirmation": "secret123",
}


@pytest.fixture(autouse=True)
def _isolated_auth_state():
    """Fresh rate-limit cache + remember store before the app builds.

    ThrottleMiddleware and the guard's login limiter bind the process-wide
    cache at construction time, and the remember store caches its ensured
    table against the database it first saw — both must be dropped before
    the per-test app (and its per-test database) comes up, and again after
    so nothing leaks into the next test.
    """
    from fastplace.auth.remember import reset_remember_store
    from fastplace.cache import reset_cache

    reset_cache()
    reset_remember_store()
    yield
    reset_cache()
    reset_remember_store()


@pytest.fixture()
async def client(sample_app):
    """A browser-playing client that tracks CSRF across token rotation.

    Login rotates the CSRF token (session-fixation defense), so the token
    captured at bootstrap goes stale after the first successful login. The
    response hook re-captures whatever the middleware advertises on every
    response; the request hook attaches the latest one to unsafe methods.
    """
    transport = httpx.ASGITransport(app=sample_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        token: list[str | None] = [None]

        async def attach_csrf(request: httpx.Request) -> None:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and token[0]:
                request.headers.setdefault("X-Fastplace-CSRF-Token", token[0])

        async def capture_csrf(response: httpx.Response) -> None:
            fresh = response.headers.get("X-Fastplace-CSRF-Token")
            if fresh:
                token[0] = fresh

        c.event_hooks["request"].append(attach_csrf)
        c.event_hooks["response"].append(capture_csrf)
        yield c


async def register_user(client) -> None:
    """Create the fixture user, then log back out.

    Registration logs in, and the ``guest`` middleware on the auth routes
    would bounce every subsequent POST /login as a redirect — tests that
    exercise the login edge need an anonymous client with an existing user.
    The trailing GET mints a fresh CSRF token the way a browser's next page
    load would: logout invalidates the session (its token included).
    """
    await client.get("/login")
    created = await client.post("/register", json=REGISTER_PAYLOAD)
    assert created.status_code == 303
    logged_out = await client.post("/logout")
    assert logged_out.status_code == 303
    await client.get("/login")


class TestRegistration:
    async def test_register_creates_the_user_and_logs_them_in(self, client):
        await client.get("/login")
        response = await client.post("/register", json=REGISTER_PAYLOAD)
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard"

        # The route-protection edge is the login proof: an authenticated
        # request reaches the page an anonymous one is bounced from.
        profile = await client.get("/settings/profile")
        assert profile.status_code == 200

    async def test_register_with_a_duplicate_email_is_a_422(self, client):
        await register_user(client)
        second = await client.post("/register", json=REGISTER_PAYLOAD)
        assert second.status_code == 422
        assert "The email has already been taken." in second.json()["errors"]["email"]


class TestLogin:
    async def test_wrong_credentials_return_the_frozen_422_contract(self, client):
        await register_user(client)
        response = await client.post(
            "/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": "wrong-pass"},
        )
        assert response.status_code == 422
        assert response.json()["errors"]["email"] == ["These credentials do not match our records."]

    async def test_sixth_failed_attempt_is_locked_out_with_retry_after(self, client):
        await register_user(client)
        for _ in range(5):
            failed = await client.post(
                "/login",
                json={"email": REGISTER_PAYLOAD["email"], "password": "wrong-pass"},
            )
            assert failed.status_code == 422
        sixth = await client.post(
            "/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": "wrong-pass"},
        )
        assert sixth.status_code == 429
        assert "Retry-After" in sixth.headers


class TestLogout:
    async def test_post_logout_redirects_to_login(self, client):
        await client.get("/login")
        await client.post("/register", json=REGISTER_PAYLOAD)
        response = await client.post("/logout")
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    async def test_get_logout_is_a_405(self, client):
        response = await client.get("/logout")
        assert response.status_code == 405


class TestGuestBounce:
    async def test_logged_in_visit_to_login_bounces_to_the_dashboard(self, client):
        await client.get("/login")
        await client.post("/register", json=REGISTER_PAYLOAD)
        response = await client.get("/login")
        assert response.status_code == 302
        assert response.headers["location"] == "/dashboard"


class TestRouteProtection:
    async def test_anonymous_settings_visit_parks_and_resumes(self, client):
        # The user is created straight through the repository: registering
        # through the endpoint would consume the parked URL on its own
        # post-registration redirect before the login edge runs.
        from app.modules.accounts.repositories.user_repository import UserRepository

        await UserRepository().create_user(
            name=REGISTER_PAYLOAD["name"],
            email=REGISTER_PAYLOAD["email"],
            password=REGISTER_PAYLOAD["password"],
        )

        parked = await client.get("/settings/profile")
        assert parked.status_code == 302
        assert parked.headers["location"] == "/login"

        await client.get("/login")
        response = await client.post(
            "/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": "secret123"},
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/settings/profile"


class TestRememberCookie:
    async def test_login_with_remember_issues_the_cookie(self, client):
        await register_user(client)
        response = await client.post(
            "/login",
            json={
                "email": REGISTER_PAYLOAD["email"],
                "password": "secret123",
                "remember": "on",
            },
        )
        assert response.status_code == 303
        set_cookies = "; ".join(response.headers.get_list("set-cookie"))
        assert REMEMBER_COOKIE.search(set_cookies)

    async def test_tampered_remember_cookie_authenticates_nothing(self, client):
        await register_user(client)
        client.cookies.set("fastplace_remember", "999|tampered-validator")
        response = await client.get("/settings/profile")
        assert response.status_code == 302
        assert response.headers["location"] == "/login"
