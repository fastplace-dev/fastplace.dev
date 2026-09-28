"""``TestResponse`` fluent assertions — pure units, no app, no database."""

from __future__ import annotations

import httpx
import pytest

from fastplace.testing import TestResponse


def _response(
    status: int = 200,
    *,
    json: dict | None = None,
    text: str | None = None,
    headers: dict[str, str] | None = None,
) -> TestResponse:
    request = httpx.Request("GET", "http://test/now")
    if json is not None:
        raw = httpx.Response(status, json=json, headers=headers, request=request)
    else:
        raw = httpx.Response(status, text=text or "", headers=headers, request=request)
    return TestResponse.wrap(raw)


class TestStatusAssertions:
    def test_ok(self):
        _response(200).assert_ok()

    def test_ok_rejects_other_statuses(self):
        with pytest.raises(AssertionError):
            _response(302).assert_ok()

    def test_explicit_status(self):
        _response(201).assert_status(201)

    def test_forbidden_not_found_unauthorized(self):
        _response(403).assert_forbidden()
        _response(404).assert_not_found()
        _response(401).assert_unauthorized()


class TestRedirectAssertions:
    def test_redirect_to_full_location(self):
        response = _response(302, headers={"location": "/login?next=/dashboard"})
        response.assert_redirect_to("/login?next=/dashboard")

    def test_redirect_to_path_ignores_query(self):
        response = _response(302, headers={"location": "/login?next=/dashboard"})
        response.assert_redirect_to("/login")

    def test_redirect_mismatch_raises(self):
        response = _response(302, headers={"location": "/home"})
        with pytest.raises(AssertionError):
            response.assert_redirect_to("/login")


class TestValidationAssertions:
    def test_validated_envelope(self):
        response = _response(
            422,
            json={"message": "The given data was invalid.", "errors": {"email": ["taken"]}},
        )
        response.assert_validated()

    def test_validated_key(self):
        response = _response(
            422,
            json={"message": "The given data was invalid.", "errors": {"email": ["taken"]}},
        )
        response.assert_validated("email")

    def test_validated_key_missing_raises(self):
        response = _response(
            422,
            json={"message": "The given data was invalid.", "errors": {"email": ["taken"]}},
        )
        with pytest.raises(AssertionError):
            response.assert_validated("name")

    def test_validated_rejects_other_statuses(self):
        with pytest.raises(AssertionError):
            _response(500, json={"message": "boom", "errors": {}}).assert_validated()


class TestJsonAssertions:
    def test_json_subset_match(self):
        _response(200, json={"a": 1, "b": {"c": 2}}).assert_json({"a": 1})

    def test_json_subset_mismatch_raises(self):
        with pytest.raises(AssertionError):
            _response(200, json={"a": 1}).assert_json({"a": 2})

    def test_json_path_dots(self):
        response = _response(200, json={"user": {"email": "a@example.test"}})
        assert response.json_path("user.email") == "a@example.test"

    def test_json_path_index(self):
        response = _response(200, json={"items": [{"name": "first"}]})
        assert response.json_path("items.0.name") == "first"

    def test_assert_json_path(self):
        _response(200, json={"user": {"email": "a@example.test"}}).assert_json_path(
            "user.email", "a@example.test"
        )

    def test_json_path_missing_raises(self):
        response = _response(200, json={"user": {}})
        with pytest.raises(AssertionError):
            response.json_path("user.email")


class TestContentAssertions:
    def test_see(self):
        _response(200, text="<h1>Welcome home</h1>").assert_see("Welcome")

    def test_dont_see(self):
        _response(200, text="all good").assert_dont_see("Error")

    def test_see_failure_raises(self):
        with pytest.raises(AssertionError):
            _response(200, text="nothing here").assert_see("Welcome")

    def test_header(self):
        _response(200, headers={"X-Custom": "yes"}).assert_header("X-Custom", "yes")

    def test_header_missing_raises(self):
        with pytest.raises(AssertionError):
            _response(200).assert_header("X-Custom", "yes")
