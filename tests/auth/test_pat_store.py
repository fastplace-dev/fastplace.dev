"""Personal access token store — id|secret, sha256-at-rest (spec §4.14)."""

from __future__ import annotations

import datetime

import pytest

from fastplace.auth.tokens import (
    PersonalAccessTokenStore,
    create_token,
    pat_store,
    reset_pat_store,
)
from fastplace.db import reset_db

UTC = datetime.UTC


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/pat.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    reset_pat_store()
    yield
    reset_db()
    reset_pat_store()


@pytest.fixture()
async def pat_rows():
    await pat_store().issue(1, "laptop", abilities=["*"])
    await pat_store().issue(1, "server", abilities=["*"])
    await pat_store().issue(2, "ci-token", abilities=["posts:read"])
    yield


class TestIssue:
    async def test_issue_returns_id_secret_pair(self):
        store = PersonalAccessTokenStore()
        plaintext = await store.issue(7, "ci-runner")
        selector, sep, secret = plaintext.partition("|")
        assert sep
        assert selector.isdigit()
        assert len(secret) == 64  # token_urlsafe(48) -> 64 url-safe chars

    async def test_create_token_is_the_store_singleton_facade(self):
        plaintext = await create_token(7, "ci-runner", abilities=["orders:read"])
        assert await pat_store().authenticate(plaintext) == (7, ["orders:read"])

    async def test_default_abilities_is_the_wildcard(self):
        plaintext = await PersonalAccessTokenStore().issue(7, "anything")
        assert await PersonalAccessTokenStore().authenticate(plaintext) == (7, ["*"])

    async def test_only_the_sha256_hash_is_stored(self):
        from fastplace.auth.tokens import _hash_token

        store = PersonalAccessTokenStore()
        plaintext = await store.issue(7, "ci-runner")
        _, _, secret = plaintext.partition("|")
        digest = _hash_token(secret)
        assert len(digest) == 64
        assert secret not in digest


class TestAuthenticate:
    async def test_round_trip_returns_user_and_abilities(self):
        store = PersonalAccessTokenStore()
        plaintext = await store.issue(7, "ci", abilities=["orders", "posts:write"])
        assert await store.authenticate(plaintext) == (7, ["orders", "posts:write"])

    async def test_authenticate_rejects_malformed_bearers(self):
        store = PersonalAccessTokenStore()
        for bad in ("", "abc", "abc|zzz", "7|", "|zzz", "7|abc|def", "999|zzz"):
            assert await store.authenticate(bad) is None

    async def test_authenticate_rejects_a_tampered_secret(self):
        store = PersonalAccessTokenStore()
        plaintext = await store.issue(7, "ci")
        selector, _, secret = plaintext.partition("|")
        tampered = f"{selector}|{secret[:-4]}AAAA"
        assert await store.authenticate(tampered) is None

    async def test_expired_token_is_rejected(self):
        store = PersonalAccessTokenStore()
        past = datetime.datetime.now(UTC) - datetime.timedelta(seconds=1)
        plaintext = await store.issue(7, "ci", expires_at=past)
        assert await store.authenticate(plaintext) is None

    async def test_issue_with_a_naive_expires_at_expires_correctly(self):
        # Pydantic accepts naive datetimes; sqlite reads back naive — the
        # comparison must normalize instead of TypeError-ing into a 500.
        store = PersonalAccessTokenStore()
        naive_past = datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
        plaintext = await store.issue(7, "ci", expires_at=naive_past)
        assert await store.authenticate(plaintext) is None
        naive_future = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        live = await store.issue(7, "ci", expires_at=naive_future)
        assert await store.authenticate(live) == (7, ["*"])

    async def test_authentication_stamps_last_used(self):
        store = PersonalAccessTokenStore()
        plaintext = await store.issue(7, "ci")
        await store.authenticate(plaintext)
        row = await store._row_for(plaintext)
        assert row.last_used_at is not None


class TestRevoke:
    async def test_revoke_kills_the_token_for_its_owner_only(self):
        store = PersonalAccessTokenStore()
        plaintext = await store.issue(7, "ci")
        assert await store.revoke(int(plaintext.partition("|")[0]), 7) is True
        assert await store.authenticate(plaintext) is None

    async def test_revoke_with_a_wrong_user_revokes_nothing(self):
        store = PersonalAccessTokenStore()
        plaintext = await store.issue(7, "ci")
        assert await store.revoke(int(plaintext.partition("|")[0]), 999) is False
        assert await store.authenticate(plaintext) is not None

    async def test_revoke_all_for_user_sweeps_only_that_user(self):
        store = PersonalAccessTokenStore()
        mine = await store.issue(7, "a")
        also_mine = await store.issue(7, "b")
        theirs = await store.issue(9, "c")
        assert await store.revoke_all_for_user(7) == 2
        assert await store.authenticate(mine) is None
        assert await store.authenticate(also_mine) is None
        assert await store.authenticate(theirs) is not None


class TestPrune:
    async def test_prune_expired_removes_only_expired_rows(self):
        import datetime as dt

        store = PersonalAccessTokenStore()
        past = dt.datetime.now(UTC) - dt.timedelta(seconds=1)
        dead = await store.issue(7, "dead", expires_at=past)
        live = await store.issue(7, "live")
        assert await store.prune_expired() == 1
        assert await store.authenticate(dead) is None
        assert await store.authenticate(live) is not None


class TestList:
    async def test_list_all_returns_every_row_newest_first(self, pat_rows):
        rows = await pat_store().list_all()
        assert [row.user_id for row in rows] == [2, 1, 1]  # seeded out of order
        assert rows[0].name == "ci-token"
        assert rows[0].abilities == ["posts:read"]

    async def test_list_for_user_scopes_to_one_owner(self, pat_rows):
        rows = await pat_store().list_for_user(1)
        assert all(row.user_id == 1 for row in rows)
        assert len(rows) == 2

    async def test_list_for_user_empty_is_an_empty_list(self):
        assert await pat_store().list_for_user(999) == []
