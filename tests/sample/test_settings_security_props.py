"""Security page shared props for the ManageTwoFactor card (R9)."""

from __future__ import annotations

import httpx
import pytest

from tests.sample.test_auth_endpoints import REGISTER_PAYLOAD


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


async def test_security_page_carries_two_factor_props(client):
    await client.get("/login")
    await client.post("/register", json=REGISTER_PAYLOAD)

    response = await client.get("/settings/security", headers={"X-Fastplace-Request": "true"})
    assert response.status_code == 200
    props = response.json()["props"]
    assert props["canManageTwoFactor"] is True
    assert props["requiresConfirmation"] is True
    assert props["twoFactorEnabled"] is False  # registered, not yet set up


async def test_two_factor_enabled_prop_flips_after_confirmation(client):
    import datetime

    from app.modules.accounts.repositories.user_repository import UserRepository

    await client.get("/login")
    await client.post("/register", json=REGISTER_PAYLOAD)
    user = await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])
    user.two_factor_confirmed_at = datetime.datetime.now(datetime.UTC)
    await user.save()

    props = (
        await client.get("/settings/security", headers={"X-Fastplace-Request": "true"})
    ).json()["props"]
    assert props["twoFactorEnabled"] is True
