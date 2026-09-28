"""FakeHttp — stub responses, record requests, assert on both."""

from __future__ import annotations

import pytest

from fastplace.testing import FakeHttp, FakeResponse


@pytest.fixture
def http() -> FakeHttp:
    return FakeHttp()


async def test_stubbed_response_comes_back(http: FakeHttp):
    http.respond("GET", "https://api.test/weather", FakeResponse(200, json={"temp": 21}))
    response = await http.get("https://api.test/weather")
    assert response.status_code == 200
    assert await response.json() == {"temp": 21}


async def test_default_response_status_is_200(http: FakeHttp):
    http.respond("GET", "https://api.test/ok")
    response = await http.get("https://api.test/ok")
    assert response.status_code == 200
    assert response.text == ""


async def test_text_and_headers_roundtrip(http: FakeHttp):
    http.respond(
        "POST", "https://api.test/echo", FakeResponse(201, text="made", headers={"X-Id": "9"})
    )
    response = await http.post("https://api.test/echo", json={"a": 1})
    assert response.status_code == 201
    assert response.text == "made"
    assert response.headers["X-Id"] == "9"


async def test_wildcard_urls_match(http: FakeHttp):
    http.respond("GET", "https://api.test/users/*", FakeResponse(200, json={"ok": True}))
    response = await http.get("https://api.test/users/42")
    assert response.status_code == 200


async def test_later_stubs_win(http: FakeHttp):
    http.respond("GET", "https://api.test/x", FakeResponse(200))
    http.respond("GET", "https://api.test/*", FakeResponse(429))
    response = await http.get("https://api.test/x")
    assert response.status_code == 429


async def test_unmatched_request_is_a_loud_error(http: FakeHttp):
    with pytest.raises(AssertionError, match="https://api.test/unstubbed"):
        await http.get("https://api.test/unstubbed")


async def test_method_must_match(http: FakeHttp):
    http.respond("GET", "https://api.test/x", FakeResponse(200))
    with pytest.raises(AssertionError):
        await http.post("https://api.test/x")


async def test_requests_are_recorded_with_bodies(http: FakeHttp):
    http.respond("POST", "https://api.test/echo", FakeResponse(200))
    await http.post("https://api.test/echo", json={"a": 1}, headers={"Authorization": "Bearer t"})
    request = http.requests[0]
    assert request.method == "POST"
    assert request.url == "https://api.test/echo"
    assert request.json == {"a": 1}
    assert request.headers["Authorization"] == "Bearer t"


async def test_all_verbs_record(http: FakeHttp):
    for verb in ("get", "put", "patch", "delete", "head", "options"):
        http.respond(verb.upper(), "https://api.test/x", FakeResponse(200))
        caller = getattr(http, verb)
        await caller("https://api.test/x")
    assert [r.method for r in http.requests] == ["GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


# -- assertions ---------------------------------------------------------------


async def test_assert_requested(http: FakeHttp):
    http.respond("GET", "https://api.test/x", FakeResponse(200))
    await http.get("https://api.test/x")
    http.assert_requested("GET", "https://api.test/x")
    http.assert_requested(url="https://api.test/x", times=1)
    with pytest.raises(AssertionError):
        http.assert_requested("POST", "https://api.test/x")
    with pytest.raises(AssertionError):
        http.assert_requested("GET", "https://api.test/x", times=2)


async def test_assert_requested_with_match(http: FakeHttp):
    http.respond("POST", "https://api.test/echo", FakeResponse(200))
    await http.post("https://api.test/echo", json={"a": 1})
    http.assert_requested("POST", match={"a": 1})
    with pytest.raises(AssertionError):
        http.assert_requested("POST", match={"a": 2})


async def test_assert_not_requested_and_count(http: FakeHttp):
    http.assert_request_count(0)
    http.respond("GET", "https://api.test/x", FakeResponse(200))
    http.assert_not_requested("GET", "https://api.test/x")
    await http.get("https://api.test/x")
    http.assert_request_count(1)
    with pytest.raises(AssertionError):
        http.assert_not_requested("GET", "https://api.test/x")
