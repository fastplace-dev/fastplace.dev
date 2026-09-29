"""Serve-time precedence — prerendered HTML ahead of the ASGI app.

The full matrix from the prerender plan: a GET navigation with no query
string gets the prerendered file; every other shape (query string, POST,
API Accept, missing file, traversal) falls through to the live app
byte-identically.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from starlette.testclient import TestClient

from fastplace.http.kernel import _install_static_mounts
from fastplace.http.prerender_static import PrerenderStaticFiles

MARKER = b"<h1>PRERENDER-MARKER</h1>"


def _make_site(tmp_path: Path) -> TestClient:
    """App shaped like the real kernel: routes first, static mounts after."""
    prerender = tmp_path / "public" / "build" / "prerender" / "docs" / "x"
    prerender.mkdir(parents=True)
    (prerender / "index.html").write_bytes(MARKER)
    (tmp_path / "public" / "logo.svg").write_bytes(b"<svg>logo</svg>")

    app = FastAPI()

    @app.get("/docs/x")
    async def live_docs() -> HTMLResponse:
        return HTMLResponse("<h1>LIVE</h1>")

    _install_static_mounts(app, tmp_path)
    app.add_middleware(
        PrerenderStaticFiles, prerender_dir=tmp_path / "public" / "build" / "prerender"
    )
    return TestClient(app)


def test_prerender_middleware_is_innermost_user_middleware():
    """get_app wires the lookup innermost — closest to the router.

    Outer placement would serve prerendered HTML without the security
    headers and compression every other HTML response carries.
    """
    from fastplace.http.kernel import get_app

    app = get_app()
    assert app.user_middleware[-1].cls is PrerenderStaticFiles


def test_prerender_middleware_off_in_dev_runtime(monkeypatch):
    """`fastplace run dev` serves live pages — one stale prerender run must
    not freeze every navigation while the developer edits."""
    from fastplace.http.kernel import get_app

    monkeypatch.setenv("FASTPLACE_RUNTIME", "dev")
    app = get_app()
    assert all(m.cls is not PrerenderStaticFiles for m in app.user_middleware)


def test_prerendered_html_served_for_get(tmp_path):
    client = _make_site(tmp_path)
    response = client.get("/docs/x", headers={"accept": "text/html"})
    assert response.status_code == 200
    assert MARKER in response.content
    assert response.headers["content-type"].startswith("text/html")


def test_query_string_falls_through(tmp_path):
    """?refresh=1 asks for fresh data — the live app answers, not the file."""
    client = _make_site(tmp_path)
    response = client.get("/docs/x", headers={"accept": "text/html"}, params={"refresh": "1"})
    assert response.status_code == 200
    assert b"LIVE" in response.content
    assert MARKER not in response.content


def test_post_falls_through(tmp_path):
    client = _make_site(tmp_path)
    response = client.post("/docs/x", headers={"accept": "text/html"})
    assert response.status_code == 405  # no POST route: today's behavior
    assert MARKER not in response.content


def test_non_html_accept_falls_through(tmp_path):
    client = _make_site(tmp_path)
    response = client.get("/docs/x", headers={"accept": "application/json"})
    assert response.status_code == 200
    assert b"LIVE" in response.content
    assert MARKER not in response.content


def test_missing_prerender_file_falls_through(tmp_path):
    client = _make_site(tmp_path)
    response = client.get("/docs/none", headers={"accept": "text/html"})
    assert response.status_code == 404
    assert MARKER not in response.content


def test_traversal_attempts_do_not_escape(tmp_path):
    client = _make_site(tmp_path)
    response = client.get("/..%2F..%2Fsecret.txt", headers={"accept": "text/html"})
    assert response.status_code in (400, 404)
    assert MARKER not in response.content


def test_capture_marker_bypasses_prerender(tmp_path):
    """`fastplace prerender` re-runs must reach the live app, not old output."""
    client = _make_site(tmp_path)
    response = client.get(
        "/docs/x",
        headers={"accept": "text/html", "x-fastplace-prerender-capture": "1"},
    )
    assert response.status_code == 200
    assert b"LIVE" in response.content
    assert MARKER not in response.content


def test_assets_unaffected(tmp_path):
    """Files under public/ keep coming from the static mounts, not prerender."""
    client = _make_site(tmp_path)
    response = client.get("/logo.svg", headers={"accept": "text/html"})
    assert response.status_code == 200
    assert response.content == b"<svg>logo</svg>"


def test_no_prerender_dir_no_startup_crash(tmp_path):
    """A project that never ran `fastplace prerender` boots and serves normally."""
    (tmp_path / "public").mkdir(parents=True)
    app = FastAPI()

    @app.get("/")
    async def home() -> HTMLResponse:
        return HTMLResponse("<h1>HOME</h1>")

    _install_static_mounts(app, tmp_path)
    # Wire the middleware like get_app does — "boots and serves normally"
    # must mean the prerender lookup ran and fell through, not that it was
    # never installed.
    app.add_middleware(
        PrerenderStaticFiles, prerender_dir=tmp_path / "public" / "build" / "prerender"
    )
    client = TestClient(app)
    response = client.get("/", headers={"accept": "text/html"})
    assert response.status_code == 200
    assert b"HOME" in response.content
