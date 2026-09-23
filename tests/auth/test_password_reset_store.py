"""Password-reset token store — hashed, one live token per email (spec §4.10)."""

from __future__ import annotations

import pytest

import fastplace.auth.passwords as passwords
from fastplace.auth.passwords import token_store


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch, tmp_path):
    """Fresh sqlite + fresh singleton before AND after (the remember-store pattern)."""
    from fastplace.db import reset_db

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/resets.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    passwords.reset_token_store()
    yield
    passwords.reset_token_store()
    reset_db()


async def test_issue_peek_consume_roundtrip():
    raw = await token_store().issue("user@example.test")
    assert await token_store().peek("user@example.test", raw) is True
    assert await token_store().consume("user@example.test", raw) is True


async def test_second_issue_invalidates_the_first():
    first = await token_store().issue("user@example.test")
    second = await token_store().issue("user@example.test")
    assert await token_store().peek("user@example.test", first) is False
    assert await token_store().consume("user@example.test", second) is True


async def test_wrong_token_peek_does_not_burn():
    raw = await token_store().issue("user@example.test")
    assert await token_store().peek("user@example.test", "totally-wrong") is False
    assert await token_store().consume("user@example.test", raw) is True


async def test_expired_token_is_unusable(monkeypatch):
    raw = await token_store().issue("user@example.test")
    monkeypatch.setattr(passwords, "expire_seconds", lambda: -1)
    assert await token_store().peek("user@example.test", raw) is False
    assert await token_store().consume("user@example.test", raw) is False


async def test_consume_burns_the_token():
    raw = await token_store().issue("user@example.test")
    assert await token_store().consume("user@example.test", raw) is True
    assert await token_store().consume("user@example.test", raw) is False


async def test_purge_expired_removes_old_rows():
    await token_store().issue("a@example.test")
    await token_store().issue("b@example.test")
    removed = await token_store().purge_expired(window_seconds=-1)
    assert removed == 2


async def test_unknown_email_peek_pays_equal_work(monkeypatch):
    calls: list[int] = []

    def spy():
        calls.append(1)
        return "spy-digest"

    monkeypatch.setattr(passwords, "_dummy_digest", spy)
    assert await token_store().peek("ghost@example.test", "some-token") is False
    assert calls == [1]
