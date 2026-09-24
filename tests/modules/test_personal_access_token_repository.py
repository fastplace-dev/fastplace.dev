"""PAT repository — app-facing CRUD over the framework store (spec §4.14)."""

from __future__ import annotations

import datetime

import pytest

UTC = datetime.UTC


@pytest.fixture(autouse=True)
def _fresh_db(_fresh_app_modules, monkeypatch, tmp_path):
    from fastplace.auth.tokens import reset_pat_store
    from fastplace.db import reset_db

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/pat-repo.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    reset_pat_store()
    yield
    reset_db()
    reset_pat_store()


@pytest.fixture()
async def accounts(_fresh_db):
    from app.modules.accounts.models.personal_access_token import PersonalAccessToken  # noqa: F401
    from app.modules.accounts.repositories.personal_access_token_repository import (
        PersonalAccessTokenRepository,
    )
    from fastplace.db import db

    await db.create_all()
    return PersonalAccessTokenRepository()


class TestCreate:
    async def test_create_returns_the_plaintext_once_and_stores_the_hash(self, accounts):
        from fastplace.auth.tokens import _hash_token

        plaintext = await accounts.create(7, "ci-runner", abilities=["orders"])
        selector, _, secret = plaintext.partition("|")
        row = await accounts._row(selector)  # test helper below
        assert row is not None
        assert row.token_hash == _hash_token(secret)
        assert secret != row.token_hash


class TestFindByIdPrefix:
    async def test_prefix_match_is_user_scoped(self, accounts):
        await accounts.create(7, "one")
        await accounts.create(7, "two")
        await accounts.create(9, "theirs")
        mine = await accounts.find_by_id_prefix(7, "1")
        assert len(mine) == 1
        assert mine[0].user_id == 7

    async def test_empty_prefix_matches_nothing(self, accounts):
        await accounts.create(7, "one")
        assert await accounts.find_by_id_prefix(7, "") == []


class TestRevoke:
    async def test_revoke_hard_deletes_the_row(self, accounts):
        from app.modules.accounts.models.personal_access_token import PersonalAccessToken

        plaintext = await accounts.create(7, "ci")
        selector = int(plaintext.partition("|")[0])
        assert await accounts.revoke(7, selector) is True
        # Even the soft-delete escape hatch sees nothing — revocation is a
        # HARD delete (a soft-deleted row would still authenticate).
        survivors = (
            await PersonalAccessToken.with_deleted().where(PersonalAccessToken.id == selector).get()
        )
        assert survivors == []

    async def test_revoke_is_owner_scoped(self, accounts):
        plaintext = await accounts.create(7, "ci")
        selector = int(plaintext.partition("|")[0])
        assert await accounts.revoke(999, selector) is False
        assert await accounts.find_by_id_prefix(7, str(selector)) != []
