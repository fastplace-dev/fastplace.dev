"""Timing parity — unknown-email logins pay the wrong-password scrypt cost."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import fastplace.auth.passwords as passwords
from fastplace.auth.guards import SessionGuard


@pytest.fixture(autouse=True)
def _fresh_cache():
    from fastplace.cache import reset_cache

    reset_cache()
    yield
    reset_cache()


@pytest.fixture()
def dummy_spy(monkeypatch):
    calls: list[int] = []

    def spy():
        calls.append(1)
        return "spy-digest"

    monkeypatch.setattr(passwords, "_dummy_digest", spy)
    return calls


class _UnknownEmailProvider:
    """Every lookup misses — the unknown-email path."""

    async def retrieve_by_credentials(self, credentials: dict[str, Any]):
        return None

    async def validate_credentials(self, user, credentials):
        return False


class _KnownUserProvider:
    """Returns a user whose hash matches 'right-pass' — the wrong-password path."""

    def __init__(self) -> None:
        from fastplace.auth.hashing import Hash

        self.user = SimpleNamespace(
            id=1, email="user@example.test", password_hash=Hash.make("right-pass")
        )

    async def retrieve_by_credentials(self, credentials: dict[str, Any]):
        return self.user

    async def validate_credentials(self, user, credentials):
        from fastplace.auth.hashing import Hash

        return Hash.check(str(credentials.get("password") or ""), user.password_hash)


def _guard(provider) -> SessionGuard:
    from fastplace.ratelimit import RateLimiter

    return SessionGuard(provider, limiter=RateLimiter(), max_attempts=5, decay=60)


async def test_unknown_email_attempt_pays_equal_work(dummy_spy):
    request = SimpleNamespace(ip="127.0.0.1")
    result = await _guard(_UnknownEmailProvider()).attempt(
        request, {"email": "ghost@example.test", "password": "secret123"}
    )
    assert result is False
    assert dummy_spy == [1]


async def test_unknown_email_attempt_when_pays_equal_work(dummy_spy):
    result = await _guard(_UnknownEmailProvider()).attempt_when(
        {},
        {"email": "ghost@example.test", "password": "secret123"},
        lambda user: True,
    )
    assert result is False
    assert dummy_spy == [1]


async def test_wrong_password_once_does_not_use_the_dummy(dummy_spy):
    result = await _guard(_KnownUserProvider()).once(
        {}, {"email": "user@example.test", "password": "wrong-pass"}
    )
    assert result is False
    assert dummy_spy == []  # the real Hash.check on the real hash ran instead


async def test_unknown_email_once_pays_equal_work(dummy_spy):
    result = await _guard(_UnknownEmailProvider()).once(
        {}, {"email": "ghost@example.test", "password": "secret123"}
    )
    assert result is False
    assert dummy_spy == [1]
