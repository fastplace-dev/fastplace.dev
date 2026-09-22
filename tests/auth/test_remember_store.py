"""T5 — rotating remember tokens (selector|validator, sha256-at-rest) (spec §4.4)."""

from __future__ import annotations

import pytest

from fastplace.auth.remember import (
    RememberTokenStore,
    reset_remember_store,
)
from fastplace.db import reset_db


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/remember.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    reset_remember_store()
    yield
    reset_db()
    reset_remember_store()


class TestRememberTokenStore:
    async def test_issue_returns_selector_validator_pair(self):
        store = RememberTokenStore()
        cookie = await store.issue(7)
        selector, _, validator = cookie.partition("|")
        assert selector.isdigit()
        assert len(validator) >= 32

    async def test_consume_returns_user_and_rotates_the_pair(self):
        store = RememberTokenStore()
        cookie = await store.issue(7)

        result = await store.consume(cookie)

        assert result is not None
        user_id, fresh_cookie = result
        assert user_id == 7
        assert fresh_cookie != cookie  # rotation: a new pair every use
        # The old cookie is dead after rotation.
        assert await store.consume(cookie) is None
        # The fresh cookie works.
        assert await store.consume(fresh_cookie) is not None

    async def test_consume_rejects_malformed_cookies(self):
        store = RememberTokenStore()
        assert await store.consume("") is None
        assert await store.consume("abc") is None
        assert await store.consume("abc|zzz") is None
        assert await store.consume("999|zzz") is None  # unknown numeric selector

    async def test_consume_rejects_a_tampered_validator(self):
        store = RememberTokenStore()
        cookie = await store.issue(7)
        selector, _, validator = cookie.partition("|")
        tampered = f"{selector}|{validator[:-4]}AAAA"
        assert await store.consume(tampered) is None

    async def test_revoke_kills_one_cookie(self):
        store = RememberTokenStore()
        cookie = await store.issue(7)
        await store.revoke(cookie)
        assert await store.consume(cookie) is None

    async def test_revoke_with_a_wrong_validator_revokes_nothing(self):
        store = RememberTokenStore()
        cookie = await store.issue(7)
        selector, _, validator = cookie.partition("|")
        await store.revoke(f"{selector}|{validator[:-4]}AAAA")
        assert await store.consume(cookie) is not None  # the real pair survived

    async def test_revoke_all_for_user_kills_every_device(self):
        store = RememberTokenStore()
        first = await store.issue(7)
        second = await store.issue(7)
        other = await store.issue(9)

        removed = await store.revoke_all_for_user(7)

        assert removed == 2
        assert await store.consume(first) is None
        assert await store.consume(second) is None
        assert await store.consume(other) is not None

    async def test_only_the_validator_hash_is_stored(self):
        store = RememberTokenStore()
        cookie = await store.issue(7)
        _, _, validator = cookie.partition("|")
        from fastplace.auth.remember import _hash_validator

        digest = _hash_validator(validator)
        assert len(digest) == 64  # sha256 hex
        assert validator not in digest
