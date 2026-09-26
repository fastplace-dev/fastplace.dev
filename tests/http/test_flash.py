"""Flash channel — one-shot status messages on the session."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from fastplace.http.flash import (
    ERRORS_FLASH_KEY,
    FLASH_SESSION_KEY,
    flash,
    flash_errors,
    pop_flashed_errors,
)
from fastplace.http.render import page_payload
from fastplace.http.request import Request


def _request(**overrides) -> Request:
    base: dict = {"session": {}, "full_path": "/x", "path": "/x"}
    base.update(overrides)
    return cast(Request, SimpleNamespace(**base))


def test_flash_writes_the_session_key():
    request = _request()
    flash(request, "Saved.")
    assert request.session[FLASH_SESSION_KEY] == "Saved."


def test_flash_tolerates_a_missing_session():
    request = cast(Request, SimpleNamespace(full_path="/x", path="/x"))
    flash(request, "Saved.")  # must not raise


def test_page_payload_pops_the_flash_into_status_once():
    request = _request()
    flash(request, "We have emailed your password reset link.")
    payload = page_payload(request, "Auth/ForgotPassword", {})
    assert payload["props"]["status"] == "We have emailed your password reset link."
    second = page_payload(request, "Auth/ForgotPassword", {})
    assert "status" not in second["props"]


def test_page_props_win_over_the_flashed_status():
    request = _request()
    flash(request, "flashed")
    payload = page_payload(request, "Auth/Login", {"status": "custom"})
    assert payload["props"]["status"] == "custom"
    assert FLASH_SESSION_KEY not in request.session


def test_page_payload_without_a_session_builds_fine():
    request = cast(Request, SimpleNamespace(full_path="/x", path="/x"))
    payload = page_payload(request, "Auth/Login", {})
    assert payload["component"] == "Auth/Login"


def test_flash_errors_writes_the_errors_key():
    request = _request()
    flash_errors(request, {"name": ["The name field is required."]})
    assert request.session[ERRORS_FLASH_KEY] == {"name": ["The name field is required."]}


def test_flash_errors_tolerates_a_missing_session():
    request = cast(Request, SimpleNamespace(full_path="/x", path="/x"))
    flash_errors(request, {"name": ["required"]})  # must not raise


def test_pop_flashed_errors_consumes_once():
    request = _request()
    flash_errors(request, {"name": ["required"]})
    assert pop_flashed_errors(request) == {"name": ["required"]}
    assert pop_flashed_errors(request) is None


def test_pop_flashed_errors_without_a_session_returns_none():
    request = cast(Request, SimpleNamespace(full_path="/x", path="/x"))
    assert pop_flashed_errors(request) is None


def test_page_payload_pops_flashed_errors_once():
    request = _request()
    flash_errors(request, {"name": ["required"]})
    payload = page_payload(request, "Auth/Login", {})
    assert payload["props"]["errors"] == {"name": ["required"]}
    second = page_payload(request, "Auth/Login", {})
    assert "errors" not in second["props"]


def test_page_props_win_over_flashed_errors():
    request = _request()
    flash_errors(request, {"name": ["required"]})
    payload = page_payload(request, "Auth/Login", {"errors": {"custom": ["wins"]}})
    assert payload["props"]["errors"] == {"custom": ["wins"]}
    assert ERRORS_FLASH_KEY not in request.session
