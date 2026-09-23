"""EnsureEmailVerifiedMiddleware — the ``verified`` route gate (spec §4.5/§4.11)."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from fastplace.auth.middleware import EnsureEmailVerifiedMiddleware
from fastplace.errors import AuthorizationError


def _request(**overrides):
    base: dict = {"user": None, "path": "/dashboard", "is_bridge": False}
    base.update(overrides)
    return SimpleNamespace(**base)


def _verified_user():
    return SimpleNamespace(id=1, email_verified_at=datetime.datetime.now(datetime.UTC))


def _unverified_user():
    return SimpleNamespace(id=1, email_verified_at=None)


class _CallNext:
    """Counts invocations — the pass-through signal."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, request):
        self.calls += 1
        return SimpleNamespace(status_code=200)


async def test_anonymous_passes_through():
    call_next = _CallNext()
    response = await EnsureEmailVerifiedMiddleware().handle(_request(), call_next)
    assert call_next.calls == 1
    assert response.status_code == 200


async def test_verified_user_passes_through():
    call_next = _CallNext()
    response = await EnsureEmailVerifiedMiddleware().handle(
        _request(user=_verified_user()), call_next
    )
    assert call_next.calls == 1
    assert response.status_code == 200


async def test_unverified_api_request_raises_403():
    call_next = _CallNext()
    with pytest.raises(AuthorizationError):
        await EnsureEmailVerifiedMiddleware().handle(
            _request(user=_unverified_user(), path="/api/v1/things"), call_next
        )
    assert call_next.calls == 0


async def test_unverified_browser_is_redirected_to_the_notice_page():
    response = await EnsureEmailVerifiedMiddleware().handle(
        _request(user=_unverified_user()), _CallNext()
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/email/verify"


async def test_unverified_bridge_request_is_redirected_too():
    response = await EnsureEmailVerifiedMiddleware().handle(
        _request(user=_unverified_user(), is_bridge=True), _CallNext()
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/email/verify"
