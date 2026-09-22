"""Request auth helpers: auth_id / check / guest (spec §4.16)."""

from __future__ import annotations

from types import SimpleNamespace

from fastplace.http.request import Request


def _request_with(user) -> Request:
    scope = {"fastplace_user": user} if user is not None else {}
    return Request(SimpleNamespace(scope=scope))


def test_auth_id_reads_the_provider_identifier():
    assert _request_with(SimpleNamespace(id=7)).auth_id == 7


def test_auth_id_is_none_for_guests():
    assert _request_with(None).auth_id is None


def test_check_and_guest_are_opposites():
    authed = _request_with(SimpleNamespace(id=7))
    anon = _request_with(None)
    assert authed.check is True and authed.guest is False
    assert anon.check is False and anon.guest is True
