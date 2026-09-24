"""Maintenance mode — the 503 middleware and state-file helpers (spec #61).

``fastplace down`` writes ``storage/framework/maintenance.json``; the kernel
wraps every app in ``MaintenanceMiddleware`` which reads that file once per
request and short-circuits with a styled 503 page. Tests build the app the
real way — ``get_app(project_root=...)`` — so the kernel install path is
what's under test, not a hand-wrapped stack.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fastplace.http.maintenance import MAINTENANCE_FILE, is_down


def _write_state(root: Path, payload: dict[str, Any] | str) -> Path:
    """Write raw or JSON state under ``root`` and return its path."""
    path = root / MAINTENANCE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


@pytest.fixture()
def routes():
    from fastplace.http import Router

    r = Router()

    async def ping(request):
        return {"ok": True}

    r.get("/ping", ping)
    return r


@pytest.fixture()
def app(routes, tmp_path):
    from fastplace.http import get_app

    return get_app(routes=routes, project_root=tmp_path)


@pytest.fixture()
async def client(app):
    import httpx

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# is_down — the state-file reader both the middleware and the CLI share
# ---------------------------------------------------------------------------


def test_is_down_absent_file_returns_none(tmp_path):
    assert is_down(tmp_path) is None


def test_is_down_parses_the_state_json(tmp_path):
    _write_state(tmp_path, {"retry": 60, "secret": "s", "refresh": 30})
    assert is_down(tmp_path) == {"retry": 60, "secret": "s", "refresh": 30}


# ---------------------------------------------------------------------------
# passthrough — no state file, the app answers normally
# ---------------------------------------------------------------------------


async def test_absent_state_file_passes_through(client):
    resp = await client.get("/ping")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_removed_state_file_restores_the_app(client, tmp_path):
    _write_state(tmp_path, {"retry": 60})
    assert (await client.get("/ping")).status_code == 503
    (tmp_path / MAINTENANCE_FILE).unlink()
    assert (await client.get("/ping")).status_code == 200


# ---------------------------------------------------------------------------
# the 503 page
# ---------------------------------------------------------------------------


async def test_down_state_returns_503_html_page(client, tmp_path):
    _write_state(tmp_path, {"retry": 60})

    resp = await client.get("/ping")
    assert resp.status_code == 503
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    assert "maintenance" in resp.text.lower()
    # The page is self-contained pre-bridge output — plain HTML document.
    assert resp.text.lstrip().startswith("<!DOCTYPE html>")
    # A status page must not be cached by intermediaries.
    assert resp.headers.get("cache-control") == "no-store"
    # Maintenance sits outside the session middleware — a blocked request
    # must not mint a session cookie on the way out.
    assert "set-cookie" not in resp.headers


async def test_retry_state_sets_retry_after_header(client, tmp_path):
    _write_state(tmp_path, {"retry": 60})
    resp = await client.get("/ping")
    assert resp.headers.get("retry-after") == "60"


async def test_state_without_retry_omits_retry_after(client, tmp_path):
    _write_state(tmp_path, {})
    resp = await client.get("/ping")
    assert resp.status_code == 503
    assert "retry-after" not in resp.headers


async def test_refresh_state_embeds_a_meta_refresh(client, tmp_path):
    _write_state(tmp_path, {"refresh": 30})
    body = (await client.get("/ping")).text
    assert 'http-equiv="refresh"' in body
    assert "30" in body


async def test_corrupt_state_file_stays_down_without_retry(client, tmp_path):
    # Fail-closed: the operator asked for maintenance; an unparseable file
    # must not silently re-open the app.
    _write_state(tmp_path, "not json {")
    resp = await client.get("/ping")
    assert resp.status_code == 503
    assert "retry-after" not in resp.headers


# ---------------------------------------------------------------------------
# the ?secret= bypass — query param or cookie, never a cookie of our own
# ---------------------------------------------------------------------------


async def test_secret_query_param_bypasses_the_503(client, tmp_path):
    _write_state(tmp_path, {"secret": "letmein", "retry": 60})
    resp = await client.get("/ping?secret=letmein")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    # The middleware verifies, it never plants a bypass cookie.
    assert "set-cookie" not in resp.headers


async def test_wrong_secret_still_gets_503(client, tmp_path):
    _write_state(tmp_path, {"secret": "letmein"})
    resp = await client.get("/ping?secret=nope")
    assert resp.status_code == 503


async def test_secret_cookie_bypasses_the_503(client, tmp_path):
    _write_state(tmp_path, {"secret": "letmein"})
    resp = await client.get("/ping", cookies={"fastplace_maintenance": "letmein"})
    assert resp.status_code == 200


async def test_secret_query_ignored_when_state_has_no_secret(client, tmp_path):
    _write_state(tmp_path, {"retry": 60})
    resp = await client.get("/ping?secret=letmein")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# raw ASGI contract — non-http scopes pass straight through
# ---------------------------------------------------------------------------


async def test_non_http_scope_passes_through(tmp_path):
    from fastplace.http.maintenance import MaintenanceMiddleware

    reached: list[str] = []

    async def inner(scope, receive, send):
        reached.append(scope["type"])

    middleware = MaintenanceMiddleware(inner, root=tmp_path)
    _write_state(tmp_path, {"retry": 60})  # even fully down…
    await middleware({"type": "websocket"}, None, None)  # …websockets pass
    assert reached == ["websocket"]
