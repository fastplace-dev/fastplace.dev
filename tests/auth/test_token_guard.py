"""T4.3 — token guard: JWT issue/verify, Authorization: Bearer, config/auth.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fastplace.auth.guards import TokenGuard, guard
from fastplace.auth.providers import DictUserProvider
from fastplace.errors import ConfigurationError

SECRET = "test-app-key-not-for-production-use-only"
USER = SimpleNamespace(id=7, name="Firoz")


def make_guard(**overrides) -> TokenGuard:
    params = {"secret": SECRET, "provider": DictUserProvider(), "ttl": 3600}
    params.update(overrides)
    guard_ = TokenGuard(provider=params.pop("provider"), **params)
    guard_.provider.add(USER)
    return guard_


class TestTokenGuardUnit:
    def test_issue_for_user_embeds_subject_and_expiry(self):
        token = make_guard().issue_for(USER)
        claims = make_guard().decode(token)
        assert claims["sub"] == "7"
        assert claims["exp"] > claims["iat"]

    def test_custom_claims_round_trip(self):
        token = make_guard().issue_for(USER, claims={"role": "admin"})
        assert make_guard().decode(token)["role"] == "admin"

    def test_expired_tokens_fail_to_decode(self):
        token = make_guard().issue_for(USER, ttl=-10)
        import jwt as pyjwt

        with pytest.raises(pyjwt.ExpiredSignatureError):
            make_guard().decode(token)

    def test_decode_rejects_tokens_signed_with_another_secret(self):
        other = make_guard(secret="attacker-controlled-secret-0123456789abcdef").issue_for(USER)
        import jwt as pyjwt

        with pytest.raises(pyjwt.PyJWTError):
            make_guard().decode(other)

    def test_decode_requires_an_expiry_claim(self):
        # A correctly signed token without exp must not authenticate forever.
        import jwt as pyjwt

        g = make_guard()
        from time import time

        no_exp = pyjwt.encode(
            {"sub": "7", "iss": g.issuer, "iat": int(time()) - 10**9},
            g.secret,
            algorithm=g.algorithm,
        )
        with pytest.raises(pyjwt.PyJWTError):
            g.decode(no_exp)

    def test_issue_rejects_overriding_registered_claims(self):
        with pytest.raises(ValueError, match="sub"):
            make_guard().issue_for(USER, claims={"sub": "999"})

    async def test_user_resolves_from_a_bearer_request(self):
        guard_ = make_guard()
        token = guard_.issue_for(USER)
        request = SimpleNamespace(
            header=lambda name, default=None: (
                f"Bearer {token}" if name == "Authorization" else default
            )
        )
        assert await guard_.user(request) is USER

    async def test_user_returns_none_without_a_bearer_header(self):
        request = SimpleNamespace(header=lambda name, default=None: default)
        assert await make_guard().user(request) is None


class TestTokenGuardHttp:
    async def test_bearer_token_resolves_request_user(
        self, auth_client, registered_user, monkeypatch
    ):
        monkeypatch.setenv("APP_KEY", SECRET)
        token = guard("token").issue_for(registered_user)
        response = await auth_client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == {"user": "Firoz"}

    async def test_expired_bearer_token_is_rejected_as_unauthorized(
        self, auth_client, registered_user, monkeypatch
    ):
        # A presented-but-invalid credential is rejected (401), never
        # silently degraded to the session identity.
        monkeypatch.setenv("APP_KEY", SECRET)
        token = guard("token").issue_for(registered_user, ttl=-10)
        response = await auth_client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    async def test_tampered_bearer_token_is_rejected_as_unauthorized(
        self, auth_client, registered_user, monkeypatch
    ):
        monkeypatch.setenv("APP_KEY", SECRET)
        token = guard("token").issue_for(registered_user) + "tamper"
        response = await auth_client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    async def test_expired_bearer_with_a_live_session_is_still_rejected(
        self, auth_client, registered_user, monkeypatch
    ):
        # Confused-principal guard: an invalid token presented alongside a
        # valid session must not fall back to the session identity.
        from tests.auth.test_session_guard import login

        monkeypatch.setenv("APP_KEY", SECRET)
        await login(auth_client)
        token = guard("token").issue_for(registered_user, ttl=-10)
        response = await auth_client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    async def test_bearer_header_wins_over_a_guest_session(
        self, auth_client, registered_user, monkeypatch
    ):
        monkeypatch.setenv("APP_KEY", SECRET)
        await auth_client.post("/login")  # session would say Firoz too — token decides
        token = guard("token").issue_for(registered_user)
        response = await auth_client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.json() == {"user": "Firoz"}


class TestGuardFactory:
    def test_default_guard_comes_from_config_auth(self, monkeypatch):
        from fastplace.auth.guards import SessionGuard

        monkeypatch.setenv("APP_KEY", SECRET)
        assert isinstance(guard(), SessionGuard)
        assert isinstance(guard("token"), TokenGuard)

    def test_token_guard_requires_an_app_key(self, monkeypatch):
        monkeypatch.delenv("APP_KEY", raising=False)
        with pytest.raises(ConfigurationError, match="APP_KEY"):
            guard("token")

    def test_unknown_guard_driver_is_a_configuration_error(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", SECRET)
        monkeypatch.setenv("AUTH_DEFAULT_GUARD", "nonsense-driver")
        with pytest.raises(ConfigurationError, match="nonsense-driver"):
            guard()
