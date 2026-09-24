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
    user.two_factor_confirmed_at = datetime.datetime.now(datetime.UTC)
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
        response = await client.post("/two-factor-challenge", json={"recovery_code": codes[0]})
        assert response.status_code == 303

        from app.modules.accounts.repositories.user_repository import UserRepository

        refreshed = await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])
        stored = json.loads(decrypt(refreshed.two_factor_recovery_codes))
        assert codes[0] not in stored
        assert len(stored) == 9

    async def test_recovery_code_single_use_no_replay(self, client):
        user, secret, codes, totp = await _park_challenge(client)
        first = await client.post("/two-factor-challenge", json={"recovery_code": codes[0]})
        assert first.status_code == 303
        # Log back out and into a fresh challenge with the SAME codes list.
        await client.post("/logout")
        await client.get("/login")
        await client.post(
            "/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        replay = await client.post("/two-factor-challenge", json={"recovery_code": codes[0]})
        assert replay.status_code == 422
        assert replay.json()["errors"]["recovery_code"] == [
            "The provided two factor authentication code is invalid."
        ]

    async def test_unknown_recovery_code_rejected(self, client):
        await _park_challenge(client)
        response = await client.post("/two-factor-challenge", json={"recovery_code": "WWWWW-WWWWW"})
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


class TestRememberCookieBoundary:
    """A remember cookie must not satisfy a confirmed 2FA challenge.

    The attack: the cookie was issued BEFORE two-factor was enabled (or was
    lifted from a pre-2FA device) and outlives the session. The remember
    fallback used to authenticate straight past the challenge — it must park
    one instead, exactly like a password login would.
    """

    async def _remember_cookie_with_2fa_confirmed(self, client):
        """Register + remember-login (2FA off), enable 2FA in the DB, then
        drop the session cookie — only the remember cookie survives."""
        import pyotp

        from app.modules.accounts.repositories.user_repository import UserRepository

        await client.get("/login")
        created = await client.post("/register", json=REGISTER_PAYLOAD)
        assert created.status_code == 303
        await client.post("/logout")  # register auto-logs-in; start clean

        await client.get("/login")
        login = await client.post(
            "/login",
            json={
                "email": REGISTER_PAYLOAD["email"],
                "password": REGISTER_PAYLOAD["password"],
                "remember": "on",
            },
        )
        assert login.status_code == 303
        assert client.cookies.get("fastplace_remember"), "remember cookie not issued"

        # Two-factor is switched on elsewhere while this device still holds
        # a perfectly valid remember cookie.
        user = await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])
        secret = generate_secret()
        user.two_factor_secret = encrypt(secret)
        user.two_factor_recovery_codes = encrypt(json.dumps(generate_recovery_codes()))
        user.two_factor_confirmed_at = datetime.datetime.now(datetime.UTC)
        await user.save()

        # The server-side session expires; the cookie is all that remains.
        client.cookies.delete("fastplace_session")
        return pyotp.TOTP(secret)

    async def test_remember_cookie_alone_cannot_skip_the_challenge(self, client):
        totp = await self._remember_cookie_with_2fa_confirmed(client)
        profile = await client.get("/settings/profile")
        # NOT 200: the fallback must not authenticate past a confirmed 2FA.
        assert profile.status_code == 302
        assert profile.headers["location"] == "/two-factor-challenge"
        # The parked challenge is real: it completes with a valid TOTP code
        # and the remember preference survives (a fresh cookie is re-issued).
        fulfilled = await client.post("/two-factor-challenge", json={"code": totp.now()})
        assert fulfilled.status_code == 303
        assert (await client.get("/settings/profile")).status_code == 200

    async def test_a_challenge_parked_by_the_fallback_is_not_re_parked_forever(self, client):
        # Hitting authed routes repeatedly while unchallenged must keep
        # steering to the challenge page (the rotated cookie survives each
        # fallback run — it must not be burned by the parking).
        await self._remember_cookie_with_2fa_confirmed(client)
        for _ in range(3):
            bounced = await client.get("/settings/profile")
            assert bounced.status_code == 302
            assert bounced.headers["location"] == "/two-factor-challenge"
        assert client.cookies.get("fastplace_remember"), "rotation must keep the cookie alive"
