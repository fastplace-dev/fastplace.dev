"""RequestIdMiddleware — inbound trust policy + response round-trip (plat-G9)."""

from __future__ import annotations

import re
import uuid

import pytest
from starlette.testclient import TestClient

from fastplace.http import Router, get_app
from fastplace.logging.context import get_request_id
from fastplace.logging.middleware import RequestIdMiddleware, sanitize_request_id

_UUID_HEX = re.compile(r"^[0-9a-f]{32}$")


def _app_with(route_handler):
    router = Router()
    router.get("/ping", route_handler)
    return get_app(routes=router)


def test_generated_id_round_trips():
    seen: dict = {}

    async def ping(request):
        seen["context_id"] = get_request_id()
        return {"pong": True}

    with TestClient(_app_with(ping)) as client:
        response = client.get("/ping")
    assert response.status_code == 200
    rid = response.headers["x-request-id"]
    assert _UUID_HEX.fullmatch(rid)
    # The endpoint observed the same id the response carries.
    assert seen["context_id"] == rid


def test_valid_inbound_header_is_trusted():
    async def ping(request):
        return {"pong": True}

    with TestClient(_app_with(ping)) as client:
        response = client.get("/ping", headers={"X-Request-ID": "frontend-trace_42"})
    assert response.headers["x-request-id"] == "frontend-trace_42"


@pytest.mark.parametrize(
    "bad",
    [
        "spaces are bad",
        "semicolon;drop",
        "a" * 65,  # over the 64-char cap
        "unicode-é",
    ],
)
def test_invalid_inbound_header_is_regenerated(bad: str):
    # Driven at the raw ASGI layer: httpx refuses non-ASCII header values
    # client-side, but the wire itself carries such bytes — the middleware
    # must regenerate from what actually arrives in the scope.
    import asyncio

    async def ping(request):
        return {"pong": True}

    app = _app_with(ping)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/ping",
        "query_string": b"",
        "headers": [(b"host", b"test"), (b"x-request-id", bad.encode("latin-1"))],
    }
    started: list = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            started.append(message)

    asyncio.run(app(scope, receive, send))
    rid = dict(started[0]["headers"])[b"x-request-id"].decode("ascii")
    assert rid != bad
    assert _UUID_HEX.fullmatch(rid)


def test_context_cleared_after_request():
    async def ping(request):
        return {"pong": True}

    with TestClient(_app_with(ping)) as client:
        client.get("/ping", headers={"X-Request-ID": "abc"})
    assert get_request_id() == ""


def test_endpoint_log_line_carries_request_id(capture_records):
    async def ping(request):
        import logging

        logging.getLogger("fastplace.test.mw").info("serving ping")
        return {"pong": True}

    with TestClient(_app_with(ping)) as client:
        client.get("/ping", headers={"X-Request-ID": "corr-77"})
    assert any("request_id=corr-77" in line for line in capture_records.lines)


def test_non_http_scope_passes_through_untouched():
    called = []

    async def inner(scope, receive, send):
        called.append(scope["type"])

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        pass

    import asyncio

    asyncio.run(
        RequestIdMiddleware(inner)({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
    )
    assert called == ["lifespan"]
    assert get_request_id() == ""


def test_sanitize_request_id_contract():
    assert sanitize_request_id("ok-ID_9") == "ok-ID_9"
    assert sanitize_request_id(" trimmed ") == "trimmed"
    assert sanitize_request_id("") == ""
    assert sanitize_request_id("bad value") == ""
    assert sanitize_request_id("x" * 64) == "x" * 64
    assert sanitize_request_id("x" * 65) == ""


def test_app_sets_its_own_header_wins():
    from fastplace.http import Json

    async def ping(request):
        return Json({"pong": True}, headers={"X-Request-ID": "app-owned"})

    with TestClient(_app_with(ping)) as client:
        response = client.get("/ping")
    # The app's own header is never overwritten by the middleware.
    assert response.headers["x-request-id"] == "app-owned"


def test_generated_ids_are_unique_per_request():
    async def ping(request):
        return {"pong": True}

    with TestClient(_app_with(ping)) as client:
        first = client.get("/ping").headers["x-request-id"]
        second = client.get("/ping").headers["x-request-id"]
    assert first != second
    uuid.UUID(hex=first)  # parses as a uuid4 hex — valid correlation id
    uuid.UUID(hex=second)
