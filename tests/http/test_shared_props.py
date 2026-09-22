"""Shared props registry (spec §4.16)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fastplace.http.render import page_payload, reset_shared_props, share


@pytest.fixture(autouse=True)
def _clean_shared():
    reset_shared_props()
    yield
    reset_shared_props()


def _request(**extra) -> SimpleNamespace:
    base = {"full_path": "/x", "session": {}, "is_bridge": False}
    base.update(extra)
    return SimpleNamespace(**base)


def test_share_merges_into_every_page_payload():
    share(lambda request: {"auth": {"user": {"id": 7}}})
    payload = page_payload(_request(), "Dashboard/Index", {"own": 1})
    assert payload["props"]["auth"] == {"user": {"id": 7}}
    assert payload["props"]["own"] == 1


def test_page_props_win_over_shared_values():
    share(lambda request: {"title": "shared"})
    payload = page_payload(_request(), "Dashboard/Index", {"title": "page"})
    assert payload["props"]["title"] == "page"


def test_non_dict_contributions_are_ignored():
    share(lambda request: None)
    payload = page_payload(_request(), "Dashboard/Index", {})
    assert payload["props"] == {}


def test_shared_values_cannot_shadow_the_csrf_token():
    share(lambda request: {"csrf_token": "forged"})
    payload = page_payload(_request(session={"_token": "real"}), "Dashboard/Index", {})
    assert payload["props"]["csrf_token"] == "real"
