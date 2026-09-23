"""Password confirmation endpoints (spec §4.12) through the sample app."""

from __future__ import annotations

import httpx
import pytest

from tests.sample.test_auth_endpoints import REGISTER_PAYLOAD  # reuse payload


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


async def logged_in_client(client) -> None:
    """Register (which logs in) and land on a fresh CSRF token."""
    await client.get("/login")
    response = await client.post("/register", json=REGISTER_PAYLOAD)
    assert response.status_code == 303


class TestConfirmPasswordPage:
    async def test_get_requires_authentication(self, client):
        response = await client.get("/user/confirm-password")
        assert response.status_code == 302
        assert response.headers["location"].startswith("/login")

    async def test_get_renders_for_authenticated_users(self, client):
        await logged_in_client(client)
        response = await client.get("/user/confirm-password")
        assert response.status_code == 200
        assert "ConfirmPassword" in response.text


class TestConfirmPasswordPost:
    async def test_correct_password_confirms_and_redirects_to_intended(self, client):
        await logged_in_client(client)
        # Park an intended destination the way password.confirm would.
        page = await client.get("/user/confirm-password")
        assert page.status_code == 200

        response = await client.post(
            "/user/confirm-password", json={"password": REGISTER_PAYLOAD["password"]}
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard"  # intended() default

    async def test_wrong_password_is_the_frozen_422(self, client):
        await logged_in_client(client)
        response = await client.post("/user/confirm-password", json={"password": "nope"})
        assert response.status_code == 422
        assert response.json()["errors"]["password"] == ["The password is incorrect."]

    async def test_missing_password_is_a_field_422(self, client):
        await logged_in_client(client)
        response = await client.post("/user/confirm-password", json={})
        assert response.status_code == 422
        # R12's frozen contract string — the frontend mock pins it, so the
        # service raises it explicitly (no translation layer exists).
        assert response.json()["errors"]["password"] == ["The password field is required."]
