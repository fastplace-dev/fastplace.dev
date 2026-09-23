"""Login interrupt for confirmed 2FA users — the bridge redirect flow."""

from __future__ import annotations

import datetime

import httpx
import pytest

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


async def client_with_confirmed_two_factor_user(client) -> None:
    """Register + confirm 2FA directly, then log out into the challenge state."""
    from app.modules.accounts.repositories.user_repository import UserRepository

    await client.get("/login")
    created = await client.post("/register", json=REGISTER_PAYLOAD)
    assert created.status_code == 303
    user = await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])
    user.two_factor_confirmed_at = datetime.datetime.now(datetime.UTC)
    await user.save()
    logged_out = await client.post("/logout")
    assert logged_out.status_code == 303
    await client.get("/login")


class TestTwoFactorLoginInterrupt:
    async def test_valid_credentials_redirect_to_the_challenge(self, client):
        await client_with_confirmed_two_factor_user(client)
        response = await client.post(
            "/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/two-factor-challenge"

    async def test_json_api_client_gets_the_two_factor_flag(self, client):
        await client_with_confirmed_two_factor_user(client)
        response = await client.post(
            "/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
            headers={"Accept": "application/json"},
        )
        # The bridge form also sends Accept: json — it carries
        # X-Fastplace-Request and gets the redirect. A bare JSON client
        # (no bridge header) gets the flag.
        assert response.headers["content-type"].startswith("application/json")
        assert response.json().get("two_factor") is True

    async def test_wrong_password_still_the_frozen_422(self, client):
        await client_with_confirmed_two_factor_user(client)
        response = await client.post(
            "/login", json={"email": REGISTER_PAYLOAD["email"], "password": "wrong"}
        )
        assert response.status_code == 422
        assert response.json()["errors"]["email"] == [
            "These credentials do not match our records."
        ]
