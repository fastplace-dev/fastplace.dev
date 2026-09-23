"""The two-factor challenge: code, recovery code, replay, and forgery."""

from __future__ import annotations

import datetime
import json

import httpx
import pytest

from fastplace.auth.encryption import decrypt, encrypt
from fastplace.auth.two_factor import generate_recovery_codes, generate_secret

REGISTER_PAYLOAD = {
    "name": "Firoz",
    "email": "firoz@example.test",
    "password": "secret123",
    "password_confirmation": "secret123",
}


@pytest.fixture(autouse=True)
def _app_key(monkeypatch: pytest.MonkeyPatch):
    # The sample .env ships an empty APP_KEY; the encrypted columns refuse
    # to work without one (same pin as tests/auth/test_encryption.py).
    monkeypatch.setenv("APP_KEY", "k" * 64)


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


async def _park_challenge(client, *, code_ok_setup=True):
    """Register, confirm 2FA with known secret+codes, log out, attempt login."""
    import pyotp

    from app.modules.accounts.repositories.user_repository import UserRepository

    await client.get("/login")
    created = await client.post("/register", json=REGISTER_PAYLOAD)
    assert created.status_code == 303

    user = await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])
    secret = generate_secret()
    codes = generate_recovery_codes()
    user.two_factor_secret = encrypt(secret)
    user.two_factor_recovery_codes = encrypt(json.dumps(codes))
    user.two_factor_confirmed_at = datetime.datetime.now(datetime.timezone.utc)
    await user.save()

    await client.post("/logout")
    await client.get("/login")
    login = await client.post(
        "/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
    )
    assert login.status_code == 303
    return user, secret, codes, pyotp.TOTP(secret)


class TestChallengeFulfillment:
    async def test_valid_totp_code_completes_login(self, client):
        user, secret, codes, totp = await _park_challenge(client)
        response = await client.post("/two-factor-challenge", json={"code": totp.now()})
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard"
        # Logged in: an authenticated page answers 200.
        assert (await client.get("/settings/profile")).status_code == 200

    async def test_invalid_code_is_the_frozen_422(self, client):
        await _park_challenge(client)
        response = await client.post("/two-factor-challenge", json={"code": "000000"})
        assert response.status_code == 422
        assert response.json()["errors"]["code"] == [
            "The provided two factor authentication code is invalid."
        ]

    async def test_recovery_code_completes_login_and_is_consumed(self, client):
        user, secret, codes, totp = await _park_challenge(client)
        response = await client.post(
            "/two-factor-challenge", json={"recovery_code": codes[0]}
        )
        assert response.status_code == 303

        from app.modules.accounts.repositories.user_repository import UserRepository

        refreshed = await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])
        stored = json.loads(decrypt(refreshed.two_factor_recovery_codes))
        assert codes[0] not in stored
        assert len(stored) == 9

    async def test_recovery_code_single_use_no_replay(self, client):
        user, secret, codes, totp = await _park_challenge(client)
        first = await client.post(
            "/two-factor-challenge", json={"recovery_code": codes[0]}
        )
        assert first.status_code == 303
        # Log back out and into a fresh challenge with the SAME codes list.
        await client.post("/logout")
        await client.get("/login")
        await client.post(
            "/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        replay = await client.post(
            "/two-factor-challenge", json={"recovery_code": codes[0]}
        )
        assert replay.status_code == 422
        assert replay.json()["errors"]["recovery_code"] == [
            "The provided two factor authentication code is invalid."
        ]

    async def test_unknown_recovery_code_rejected(self, client):
        await _park_challenge(client)
        response = await client.post(
            "/two-factor-challenge", json={"recovery_code": "WWWWW-WWWWW"}
        )
        assert response.status_code == 422

    async def test_no_parked_challenge_redirects_to_login(self, client):
        # No challenge in flight — POSTing the endpoint must neither leak
        # anything nor 500; the guest bounce to /login is the safe answer.
        await client.get("/login")
        response = await client.post("/two-factor-challenge", json={"code": "123456"})
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    async def test_missing_both_fields_is_the_422(self, client):
        await _park_challenge(client)
        response = await client.post("/two-factor-challenge", json={})
        assert response.status_code == 422
        assert response.json()["errors"]["code"] == [
            "The provided two factor authentication code is invalid."
        ]

    async def test_garbage_body_is_the_frozen_422(self, client):
        # An undecodable body is a client fault — the endpoint answers the
        # frozen contract, never a 500 (validate()'s decode precedent).
        await _park_challenge(client)
        response = await client.post("/two-factor-challenge", content="not-json{")
        assert response.status_code == 422
        assert response.json()["errors"]["code"] == [
            "The provided two factor authentication code is invalid."
        ]

    async def test_get_challenge_page_is_guest_guarded(self, client):
        await _park_challenge(client)
        await client.post("/two-factor-challenge", json={"code": "000000"})  # stay anon
        page = await client.get("/two-factor-challenge")
        assert page.status_code == 200
        assert "TwoFactorChallenge" in page.text
