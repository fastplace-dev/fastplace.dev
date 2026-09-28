"""dev_shell — the `run dev` entrypoint that survives a broken import.

A Python syntax error used to kill the uvicorn worker while the reloader
kept the socket: every request hung until the file was fixed (audit
supp-2-G5). The shell imports the project app through a guard and serves a
self-refreshing 503 page when the import fails.
"""

from __future__ import annotations

import sys

import pytest
from starlette.testclient import TestClient

_GOOD_ASGI = """
async def app(scope, receive, send):
    if scope["type"] == "http":
        body = b"REAL APP"
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": body})
"""

_BROKEN_ASGI = "app = (  # unclosed parenthesis → SyntaxError on import\n"


@pytest.fixture()
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.syspath_prepend(str(root))
    yield root
    sys.modules.pop("asgi", None)
    sys.modules.pop("fastplace.http.dev_shell", None)


def _import_shell():
    sys.modules.pop("asgi", None)
    sys.modules.pop("fastplace.http.dev_shell", None)
    import fastplace.http.dev_shell as shell

    importlib_reload = __import__("importlib").reload
    importlib_reload(shell)
    return shell


def test_shell_delegates_to_the_project_app(project):
    (project / "asgi.py").write_text(_GOOD_ASGI)
    shell = _import_shell()
    resp = TestClient(shell.app).get("/anything")
    assert resp.status_code == 200
    assert resp.text == "REAL APP"


def test_shell_serves_503_when_the_project_fails_to_import(project):
    (project / "asgi.py").write_text(_BROKEN_ASGI)
    shell = _import_shell()
    resp = TestClient(shell.app).get("/dashboard")
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("text/html")
    # The page says what happened and refreshes itself back to health.
    assert "reload" in resp.text.lower()
    assert "SyntaxError" in resp.text
    assert 'http-equiv="refresh"' in resp.text
    assert "<html lang=" in resp.text


def test_shell_handles_the_lifespan_protocol_when_broken(project):
    (project / "asgi.py").write_text(_BROKEN_ASGI)
    shell = _import_shell()
    with TestClient(shell.app) as client:  # entering runs lifespan startup
        assert client.get("/").status_code == 503


def test_shell_recovers_after_the_file_is_fixed(project):
    (project / "asgi.py").write_text(_BROKEN_ASGI)
    broken = _import_shell()
    assert TestClient(broken.app).get("/").status_code == 503

    # The uvicorn reload restarts the process, which re-imports the shell —
    # a fresh import with the file fixed must find the real app again.
    (project / "asgi.py").write_text(_GOOD_ASGI)
    healed = _import_shell()
    assert TestClient(healed.app).get("/").text == "REAL APP"
