"""Two-factor management endpoints — password.confirm gated (spec §4.13)."""

from __future__ import annotations

import json

import httpx
import pytest

from fastplace.auth.encryption import decrypt

REGISTER_PAYLOAD = {
    "name": "Firoz",
    "email": "firoz@example.test",
    "password": "secret123",
    "password_confirmation": "secret123",
}


@pytest.fixture(autouse=True)
def _app_key(monkeypatch: pytest.MonkeyPatch):
    # The sample .env ships APP_KEY empty; enable/confirm encrypt at rest
    # and refuse without one (Task 7's idiom, test_two_factor_challenge.py).
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


async def _confirmed_client(client, *, password_confirmed=True):
    """Logged-in user; optionally with a live password-confirmation window."""
    from app.modules.accounts.repositories.user_repository import UserRepository

    await client.get("/login")
    created = await client.post("/register", json=REGISTER_PAYLOAD)
    assert created.status_code == 303
    if password_confirmed:
        confirmed = await client.post(
            "/user/confirm-password", json={"password": REGISTER_PAYLOAD["password"]}
        )
        assert confirmed.status_code == 303
    return await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])


class TestPasswordConfirmGate:
    async def test_management_endpoint_bounces_without_recent_confirmation(self, client):
        await _confirmed_client(client, password_confirmed=False)
        # PR2 ruling: follow the 302 onto the confirm page — the bridge
        # fetch lands on the confirm page's bridge payload, NOT an ok JSON
        # envelope (per-request follow; the fixture client does not follow).
        response = await client.post(
            "/user/two-factor-authentication",
            headers={"X-Fastplace-Request": "true"},  # bridge POST → 302 follow
            follow_redirects=True,
        )
        assert response.json().get("component") == "Auth/ConfirmPassword"

    async def test_json_client_gets_403_envelope(self, client):
        await _confirmed_client(client, password_confirmed=False)
        response = await client.post(
            "/user/two-factor-authentication", headers={"Accept": "application/json"}
        )
        assert response.status_code == 403


class TestEnableConfirmDisable:
    async def test_enable_stores_encrypted_pending_state(self, client):
        from app.modules.accounts.repositories.user_repository import UserRepository

        await _confirmed_client(client)
        response = await client.post(
            "/user/two-factor-authentication",
            headers={"X-Fastplace-Request": "true"},
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}

        user = await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])
        assert user.two_factor_secret is not None
        assert user.two_factor_secret.startswith("fpaes1.")
        codes = json.loads(decrypt(user.two_factor_recovery_codes))
        assert len(codes) == 10
        assert user.two_factor_confirmed_at is None  # pending until confirmed

    async def test_qr_and_secret_payloads_shape(self, client):
        await _confirmed_client(client)
        await client.post("/user/two-factor-authentication")
        qr = await client.get("/user/two-factor-qr-code")
        assert qr.status_code == 200
        payload = qr.json()
        assert payload["svg"].lstrip().startswith("<?xml")
        assert payload["url"].startswith("otpauth://totp/")

        key = await client.get("/user/two-factor-secret-key")
        assert key.json()["secretKey"]

    async def test_recovery_codes_get_returns_bare_array(self, client):
        await _confirmed_client(client)
        await client.post("/user/two-factor-authentication")
        codes = await client.get("/user/two-factor-recovery-codes")
        assert codes.status_code == 200
        assert isinstance(codes.json(), list) and len(codes.json()) == 10

    async def test_confirm_with_valid_code_confirms(self, client):
        import pyotp

        from app.modules.accounts.repositories.user_repository import UserRepository

        await _confirmed_client(client)
        await client.post("/user/two-factor-authentication")
        user = await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])
        code = pyotp.TOTP(decrypt(user.two_factor_secret)).now()

        response = await client.post(
            "/user/confirmed-two-factor-authentication",
            json={"code": code},
            headers={"X-Fastplace-Request": "true"},
        )
        assert response.status_code == 200
        refreshed = await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])
        assert refreshed.two_factor_confirmed_at is not None

    async def test_confirm_with_invalid_code_is_frozen_422(self, client):
        await _confirmed_client(client)
        await client.post("/user/two-factor-authentication")
        response = await client.post(
            "/user/confirmed-two-factor-authentication",
            json={"code": "000000"},
            headers={"X-Fastplace-Request": "true"},
        )
        assert response.status_code == 422
        assert response.json()["errors"]["code"] == [
            "The provided two factor authentication code is invalid."
        ]

    async def test_disable_clears_all_three_columns(self, client):
        from app.modules.accounts.repositories.user_repository import UserRepository

        await _confirmed_client(client)
        await client.post("/user/two-factor-authentication")
        response = await client.delete(
            "/user/two-factor-authentication", headers={"X-Fastplace-Request": "true"}
        )
        assert response.status_code == 200
        user = await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])
        assert user.two_factor_secret is None
        assert user.two_factor_recovery_codes is None
        assert user.two_factor_confirmed_at is None

    async def test_regenerate_mints_a_fresh_batch_of_ten(self, client):
        await _confirmed_client(client)
        await client.post("/user/two-factor-authentication")
        first = (await client.get("/user/two-factor-recovery-codes")).json()
        second = (await client.post("/user/two-factor-recovery-codes")).json()
        assert len(second) == 10
        assert set(first).isdisjoint(set(second))


class TestReadBeforeEnable:
    """A live password-confirmation window does not imply a pending secret."""

    async def test_qr_code_before_enable_is_frozen_422(self, client):
        await _confirmed_client(client)
        response = await client.get("/user/two-factor-qr-code")
        assert response.status_code == 422
        assert response.json()["errors"]["code"] == [
            "The provided two factor authentication code is invalid."
        ]

    async def test_secret_key_before_enable_is_frozen_422(self, client):
        await _confirmed_client(client)
        response = await client.get("/user/two-factor-secret-key")
        assert response.status_code == 422
        assert response.json()["errors"]["code"] == [
            "The provided two factor authentication code is invalid."
        ]


class TestFeatureFlag:
    async def test_disabled_flag_hides_every_endpoint(self, client, monkeypatch):
        monkeypatch.setenv("TWO_FACTOR_ENABLED", "false")
        from fastplace.config import reset_config

        reset_config()  # the real reload API (tests/ai/test_vectors.py idiom)
        try:
            await _confirmed_client(client)
            for method, path in (
                ("post", "/user/two-factor-authentication"),
                ("get", "/user/two-factor-qr-code"),
                ("get", "/user/two-factor-secret-key"),
                ("post", "/user/confirmed-two-factor-authentication"),
                ("post", "/user/two-factor-recovery-codes"),
                ("get", "/user/two-factor-recovery-codes"),
                ("delete", "/user/two-factor-authentication"),
            ):
                response = await getattr(client, method)(path)
                assert response.status_code == 404, (method, path)
        finally:
            reset_config()
