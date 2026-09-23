"""PAT endpoints end-to-end through the sample app (spec §4.14/§4.19)."""

from __future__ import annotations

import datetime
import re

import httpx
import pytest

REMEMBER_PLAINTEXT = re.compile(r"^\d+\|[A-Za-z0-9_-]{64}$")
REGISTER_PAYLOAD = {
    "name": "Firoz",
    "email": "firoz@example.test",
    "password": "secret123",
    "password_confirmation": "secret123",
}
BAD_CREDENTIALS = "These credentials do not match our records."
TWO_FACTOR_MESSAGE = "Two-factor authentication is enabled on this account."


@pytest.fixture(autouse=True)
def _isolated_pat_state(monkeypatch: pytest.MonkeyPatch):
    """Fresh rate-limit cache, remember store, AND PAT store before/after.

    APP_KEY: the bearer edge resolves through the ``token`` guard, which
    refuses to construct without a signing secret — PATs are DB-backed
    hashes and never touch it, but the guard gate is coarse. The sample-app
    fixture loads no .env, so the suite would silently fall back to the
    session identity and never exercise the Bearer paths under test.
    """
    monkeypatch.setenv("APP_KEY", "pat-endpoint-test-secret-not-for-production")
    from fastplace.auth.remember import reset_remember_store
    from fastplace.auth.tokens import reset_pat_store
    from fastplace.cache import reset_cache

    reset_cache()
    reset_remember_store()
    reset_pat_store()
    yield
    reset_cache()
    reset_remember_store()
    reset_pat_store()


@pytest.fixture()
async def client(sample_app):
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


async def _login(client) -> None:
    from app.modules.accounts.repositories.user_repository import UserRepository

    await UserRepository().create_user(
        name=REGISTER_PAYLOAD["name"],
        email=REGISTER_PAYLOAD["email"],
        password=REGISTER_PAYLOAD["password"],
    )
    await client.get("/login")
    response = await client.post(
        "/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
    )
    assert response.status_code == 303


def _auth(plaintext: str) -> dict:
    return {"Authorization": f"Bearer {plaintext}"}


class TestIssueEndpoint:
    async def test_anonymous_issue_is_a_401(self, client):
        # CSRF rides the session edge and runs before the route's auth
        # middleware — a bare session-less POST is a 419 (pinned in
        # tests/auth/test_csrf.py), so mint the token first and prove the
        # 401 comes from auth, not from a missing CSRF replay.
        await client.get("/login")
        response = await client.post("/api/tokens", json={"name": "ci"})
        assert response.status_code == 401

    async def test_issue_without_a_csrf_token_is_a_419(self, sample_app):
        # The management endpoint rides the session edge — only /api/token
        # (anonymous by design) is CSRF-exempt.
        transport = httpx.ASGITransport(app=sample_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as bare:
            await bare.get("/login")  # session exists, but no CSRF replay
            response = await bare.post("/api/tokens", json={"name": "ci"})
        assert response.status_code == 419

    async def test_issue_returns_the_plaintext_once(self, client):
        await _login(client)
        response = await client.post("/api/tokens", json={"name": "ci-runner"})
        assert response.status_code == 201
        body = response.json()
        assert REMEMBER_PLAINTEXT.match(body["token"])
        assert body["abilities"] == ["*"]
        # The stored row carries the hash, never the secret.
        from app.modules.accounts.models.personal_access_token import PersonalAccessToken

        rows = await PersonalAccessToken.all()
        assert len(rows) == 1
        assert rows[0].token_hash != body["token"]
        assert body["token"].partition("|")[2] not in rows[0].token_hash

    async def test_issued_bearer_authenticates_requests(self, client):
        await _login(client)
        issued = (await client.post("/api/tokens", json={"name": "ci"})).json()
        profile = await client.get("/settings/profile", headers=_auth(issued["token"]))
        assert profile.status_code == 200

    async def test_past_expiry_is_a_422(self, client):
        await _login(client)
        past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
        response = await client.post("/api/tokens", json={"name": "ci", "expires_at": past})
        assert response.status_code == 422
        assert "future" in response.json()["errors"]["expires_at"][0]

    async def test_future_expiry_is_returned_as_an_iso_string(self, client):
        await _login(client)
        future = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        response = await client.post("/api/tokens", json={"name": "ci", "expires_at": future})
        assert response.status_code == 201
        assert response.json()["expires_at"] == future

    async def test_expired_bearer_is_a_401(self, client):
        from fastplace.auth.tokens import create_token

        await _login(client)
        from app.modules.accounts.repositories.user_repository import UserRepository

        user = await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])
        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
        plaintext = await create_token(user.id, "already-dead", expires_at=past)
        profile = await client.get("/settings/profile", headers=_auth(plaintext))
        assert profile.status_code == 401


class TestDestroyEndpoint:
    async def test_revoked_bearer_stops_authenticating(self, client):
        await _login(client)
        issued = (await client.post("/api/tokens", json={"name": "ci"})).json()
        token_id = issued["token"].partition("|")[0]
        destroyed = await client.delete(f"/api/tokens/{token_id}")
        assert destroyed.status_code == 200
        assert destroyed.json() == {"ok": True}
        profile = await client.get("/settings/profile", headers=_auth(issued["token"]))
        assert profile.status_code == 401

    async def test_destroy_of_a_foreign_token_is_a_404(self, client):
        from app.modules.accounts.repositories.user_repository import UserRepository
        from fastplace.auth.tokens import create_token

        await UserRepository().create_user(
            name="Other", email="other@example.test", password="secret123"
        )
        other = await UserRepository().find_by_email("other@example.test")
        plaintext = await create_token(other.id, "theirs")
        token_id = plaintext.partition("|")[0]
        await _login(client)  # Firoz
        destroyed = await client.delete(f"/api/tokens/{token_id}")
        assert destroyed.status_code == 404
        # The foreign token still works for its owner.
        profile = await client.get("/settings/profile", headers=_auth(plaintext))
        assert profile.status_code == 200

    async def test_destroy_with_a_non_numeric_id_is_a_404(self, client):
        await _login(client)
        destroyed = await client.delete("/api/tokens/not-a-number")
        assert destroyed.status_code == 404


class TestMobileEndpoint:
    async def test_mobile_issuance_needs_no_csrf_token(self, sample_app):
        transport = httpx.ASGITransport(app=sample_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as bare:
            from app.modules.accounts.repositories.user_repository import UserRepository

            await UserRepository().create_user(
                name=REGISTER_PAYLOAD["name"],
                email=REGISTER_PAYLOAD["email"],
                password=REGISTER_PAYLOAD["password"],
            )
            response = await bare.post(
                "/api/token",
                json={
                    "email": REGISTER_PAYLOAD["email"],
                    "password": REGISTER_PAYLOAD["password"],
                    "device_name": "pixel",
                },
            )
        assert response.status_code == 201
        assert REMEMBER_PLAINTEXT.match(response.json()["token"])

    async def test_wrong_password_gets_the_frozen_generic_error(self, client):
        await _login(client)
        await client.post("/logout")
        response = await client.post(
            "/api/token",
            json={
                "email": REGISTER_PAYLOAD["email"],
                "password": "wrong-pass",
                "device_name": "pixel",
            },
        )
        assert response.status_code == 422
        assert response.json()["errors"]["email"] == [BAD_CREDENTIALS]

    async def test_unknown_email_gets_the_same_frozen_error(self, client):
        response = await client.post(
            "/api/token",
            json={"email": "ghost@example.test", "password": "wrong-pass", "device_name": "x"},
        )
        assert response.status_code == 422
        assert response.json()["errors"]["email"] == [BAD_CREDENTIALS]

    async def test_sixth_attempt_is_locked_out_with_retry_after(self, client):
        payload = {
            "email": REGISTER_PAYLOAD["email"],
            "password": "wrong-pass",
            "device_name": "pixel",
        }
        for _ in range(5):
            assert (await client.post("/api/token", json=payload)).status_code == 422
        sixth = await client.post("/api/token", json=payload)
        assert sixth.status_code == 429
        assert "Retry-After" in sixth.headers

    async def test_two_factor_account_is_refused(self, client):
        import datetime as dt

        from app.modules.accounts.repositories.user_repository import UserRepository

        await UserRepository().create_user(
            name="Firoz", email=REGISTER_PAYLOAD["email"], password="secret123"
        )
        user = await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])
        user.two_factor_confirmed_at = dt.datetime.now(dt.UTC)
        await user.save()
        response = await client.post(
            "/api/token",
            json={
                "email": REGISTER_PAYLOAD["email"],
                "password": REGISTER_PAYLOAD["password"],
                "device_name": "pixel",
            },
        )
        assert response.status_code == 422
        assert response.json()["errors"]["email"] == [TWO_FACTOR_MESSAGE]


class TestInvalidBearer:
    async def test_invalid_bearer_is_a_401(self, client):
        response = await client.get(
            "/settings/profile", headers=_auth("999|tampered-secret-00000000000000000000")
        )
        assert response.status_code == 401
