"""The browser-log HTTP sink: POST /_fastplace/browser-logs → ring buffer."""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from fastplace.mcp.browser import reset_buffer
from fastplace.mcp.browser_log_mount import (
    BROWSER_LOG_MOUNT_PATH,
    build_browser_log_app,
    mount_browser_logs,
)


@pytest.fixture(autouse=True)
def fresh_buffer():
    reset_buffer()
    yield
    reset_buffer()


class _StubApp:
    def __init__(self):
        self.mounts: list[tuple[str, object]] = []

    def mount(self, path, app):
        self.mounts.append((path, app))


def test_build_returns_none_when_flag_off(monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_BROWSER_LOGS", "0")
    monkeypatch.setenv("APP_DEBUG", "1")
    assert build_browser_log_app() is None


def test_build_returns_none_when_not_debug(monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_BROWSER_LOGS", "1")
    monkeypatch.delenv("APP_DEBUG", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    assert build_browser_log_app() is None


def test_build_mounts_in_local_debug(monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_BROWSER_LOGS", "1")
    monkeypatch.setenv("APP_DEBUG", "1")
    app = build_browser_log_app()
    assert app is not None


def test_mount_is_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_BROWSER_LOGS", "0")
    stub = _StubApp()
    mount_browser_logs(stub)
    assert stub.mounts == []


def test_mount_uses_fixed_path(monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_BROWSER_LOGS", "1")
    monkeypatch.setenv("APP_DEBUG", "1")
    stub = _StubApp()
    mount_browser_logs(stub)
    assert [p for p, _ in stub.mounts] == [BROWSER_LOG_MOUNT_PATH]
    assert BROWSER_LOG_MOUNT_PATH == "/_fastplace/browser-logs"


def test_post_entries_land_in_buffer(monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_BROWSER_LOGS", "1")
    monkeypatch.setenv("APP_DEBUG", "1")
    app = build_browser_log_app()
    client = TestClient(app)

    response = client.post(
        BROWSER_LOG_MOUNT_PATH,
        json={
            "entries": [
                {"level": "error", "message": "boom", "url": "http://x/y"},
                {"level": "warn", "message": "meh"},
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": 2}
    # process buffer, not a fresh instance, holds the entries
    from fastplace.mcp.browser import get_buffer

    tail = get_buffer().tail(10)
    assert [e["message"] for e in tail] == ["boom", "meh"]
    assert tail[0]["level"] == "error"
    assert tail[0]["url"] == "http://x/y"


def test_get_serves_capture_script(monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_BROWSER_LOGS", "1")
    monkeypatch.setenv("APP_DEBUG", "1")
    client = TestClient(build_browser_log_app())

    response = client.get(BROWSER_LOG_MOUNT_PATH)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert "console" in response.text
    assert "unhandledrejection" in response.text


def test_put_rejected(monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_BROWSER_LOGS", "1")
    monkeypatch.setenv("APP_DEBUG", "1")
    client = TestClient(build_browser_log_app())

    assert client.put(BROWSER_LOG_MOUNT_PATH, json={}).status_code == 405


def test_malformed_json_rejected(monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_BROWSER_LOGS", "1")
    monkeypatch.setenv("APP_DEBUG", "1")
    client = TestClient(build_browser_log_app())

    response = client.post(
        BROWSER_LOG_MOUNT_PATH, content=b"not json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 400


def test_non_list_entries_rejected(monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_BROWSER_LOGS", "1")
    monkeypatch.setenv("APP_DEBUG", "1")
    client = TestClient(build_browser_log_app())

    response = client.post(BROWSER_LOG_MOUNT_PATH, json={"entries": "nope"})

    assert response.status_code == 400


def test_entries_missing_message_rejected(monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_BROWSER_LOGS", "1")
    monkeypatch.setenv("APP_DEBUG", "1")
    client = TestClient(build_browser_log_app())

    response = client.post(BROWSER_LOG_MOUNT_PATH, json={"entries": [{"level": "error"}]})

    assert response.status_code == 400


def test_oversized_body_rejected(monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_BROWSER_LOGS", "1")
    monkeypatch.setenv("APP_DEBUG", "1")
    client = TestClient(build_browser_log_app())

    response = client.post(
        BROWSER_LOG_MOUNT_PATH,
        content=json.dumps({"entries": [{"message": "x" * 2_000_000}]}).encode(),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413


def test_empty_entries_accepted(monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_BROWSER_LOGS", "1")
    monkeypatch.setenv("APP_DEBUG", "1")
    client = TestClient(build_browser_log_app())

    response = client.post(BROWSER_LOG_MOUNT_PATH, json={"entries": []})

    assert response.status_code == 200
    assert response.json() == {"accepted": 0}
