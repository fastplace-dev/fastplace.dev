"""PAT service — issue/revoke at the API edge (spec §4.14)."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from fastplace.errors import NotFoundError, ValidationError

UTC = datetime.UTC
BAD_CREDENTIALS = "These credentials do not match our records."
TWO_FACTOR_MESSAGE = "Two-factor authentication is enabled on this account."


@pytest.fixture(autouse=True)
def _fresh_db(_fresh_app_modules, monkeypatch, tmp_path):
    from fastplace.auth.tokens import reset_pat_store
    from fastplace.db import reset_db

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/pat-service.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    reset_pat_store()
    yield
    reset_db()
    reset_pat_store()


@pytest.fixture(autouse=True)
def _clean_listeners():
    from fastplace.events import reset_listeners

    reset_listeners()
    yield
    reset_listeners()


@pytest.fixture(autouse=True)
def _fresh_cache():
    from fastplace.cache import reset_cache

    reset_cache()
    yield
    reset_cache()


@pytest.fixture()
async def accounts(_fresh_db):
    from app.modules.accounts.models.personal_access_token import PersonalAccessToken
    from app.modules.accounts.repositories.user_repository import UserRepository
    from app.modules.accounts.services.personal_access_token_service import (
        PersonalAccessTokenService,
    )
    from fastplace.db import db

    await db.create_all()
    user = await UserRepository().create_user(
        name="Firoz", email="firoz@example.test", password="secret123"
    )
    return SimpleNamespace(
        service=PersonalAccessTokenService(),
        user=user,
        PersonalAccessToken=PersonalAccessToken,
        UserRepository=UserRepository,
    )


def _request_for(user) -> SimpleNamespace:
    return SimpleNamespace(user=user, auth_id=user.id, scope={})


class TestIssue:
    async def test_issue_returns_the_plaintext_once(self, accounts):
        data = {"name": "ci-runner", "abilities": ["orders"], "expires_at": None}
        issued = await accounts.service.issue(_request_for(accounts.user), data)
        assert issued["token"].partition("|")[0].isdigit()
        assert issued["abilities"] == ["orders"]
        assert issued["name"] == "ci-runner"

    async def test_issue_defaults_abilities_to_the_wildcard(self, accounts):
        issued = await accounts.service.issue(
            _request_for(accounts.user), {"name": "ci", "abilities": None, "expires_at": None}
        )
        assert issued["abilities"] == ["*"]

    async def test_issue_rejects_a_past_expiry(self, accounts):
        past = datetime.datetime.now(UTC) - datetime.timedelta(seconds=1)
        with pytest.raises(ValidationError) as exc_info:
            await accounts.service.issue(
                _request_for(accounts.user),
                {"name": "ci", "abilities": None, "expires_at": past},
            )
        assert "future" in str(exc_info.value.errors["expires_at"][0])


class TestRevoke:
    async def test_reject_rejects_a_non_numeric_id(self, accounts):
        with pytest.raises(NotFoundError):
            await accounts.service.revoke(_request_for(accounts.user), "not-a-number")

    async def test_revoke_of_another_users_token_is_not_found(self, accounts):
        other = await accounts.UserRepository().create_user(
            name="Other", email="other@example.test", password="secret123"
        )
        plaintext = await accounts.service.issue(
            _request_for(other), {"name": "ci", "abilities": None, "expires_at": None}
        )
        token_id = plaintext["token"].partition("|")[0]
        with pytest.raises(NotFoundError):
            await accounts.service.revoke(_request_for(accounts.user), token_id)

    async def test_revoke_of_a_valid_token_succeeds(self, accounts):
        plaintext = await accounts.service.issue(
            _request_for(accounts.user), {"name": "ci", "abilities": None, "expires_at": None}
        )
        token_id = plaintext["token"].partition("|")[0]
        await accounts.service.revoke(_request_for(accounts.user), token_id)
        survivors = (
            await accounts.PersonalAccessToken.with_deleted()
            .where(accounts.PersonalAccessToken.id == int(token_id))
            .get()
        )
        assert survivors == []


class TestIssueMobile:
    async def test_valid_credentials_issue_a_working_token(self, accounts):
        request = SimpleNamespace(user=None, auth_id=None, scope={})
        issued = await accounts.service.issue_mobile(
            request,
            {"email": "firoz@example.test", "password": "secret123", "device_name": "pixel"},
        )
        assert issued["token"].partition("|")[0].isdigit()
        assert issued["name"] == "pixel"

    async def test_wrong_password_gets_the_frozen_generic_error(self, accounts):
        request = SimpleNamespace(user=None, auth_id=None, scope={})
        with pytest.raises(ValidationError) as exc_info:
            await accounts.service.issue_mobile(
                request,
                {"email": "firoz@example.test", "password": "wrong-pass", "device_name": "pixel"},
            )
        assert exc_info.value.errors["email"] == [BAD_CREDENTIALS]

    async def test_unknown_email_gets_the_same_error_and_pays_the_hash_cost(
        self, accounts, monkeypatch
    ):
        calls: list[str] = []

        def fake_check(value, digest):
            calls.append(value)

        import fastplace.auth.hashing as hashing_module

        monkeypatch.setattr(hashing_module.Hash, "check", staticmethod(fake_check))
        request = SimpleNamespace(user=None, auth_id=None, scope={})
        with pytest.raises(ValidationError) as exc_info:
            await accounts.service.issue_mobile(
                request,
                {"email": "ghost@example.test", "password": "wrong-pass", "device_name": "pixel"},
            )
        assert exc_info.value.errors["email"] == [BAD_CREDENTIALS]
        assert calls == ["wrong-pass"]  # dummy-digest parity work happened

    async def test_two_factor_confirmed_accounts_are_refused(self, accounts):
        import datetime as dt

        accounts.user.two_factor_confirmed_at = dt.datetime.now(UTC)
        await accounts.user.save()
        request = SimpleNamespace(user=None, auth_id=None, scope={})
        with pytest.raises(ValidationError) as exc_info:
            await accounts.service.issue_mobile(
                request,
                {"email": "firoz@example.test", "password": "secret123", "device_name": "pixel"},
            )
        assert exc_info.value.errors["email"] == [TWO_FACTOR_MESSAGE]
