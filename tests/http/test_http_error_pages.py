"""Browser-facing HTTP error pages (serve-G7 / a11y1-G3 / supp-2-G10).

Navigations (``Accept: text/html``) get a styled HTML page with a proper
title and language attribute; API clients and the SPA bridge keep the JSON
contract. Apps can override any status with ``public/errors/{status}.html``,
and headers carried by the exception (``Retry-After``) survive both paths.
"""

from __future__ import annotations

import pytest

from fastplace.http import Router, get_app, lifecycle


@pytest.fixture()
def routes() -> Router:
    r = Router()

    async def ping(request):  # noqa: ANN001
        return {"pong": True}

    async def teapot(request):  # noqa: ANN001
        from starlette.exceptions import HTTPException

        raise HTTPException(status_code=418, detail="teapot", headers={"Retry-After": "7"})

    async def forbidden(request):  # noqa: ANN001
        from fastplace.errors import AuthorizationError

        raise AuthorizationError("not allowed")

    async def boom(request):  # noqa: ANN001
        raise RuntimeError("kaboom")

    r.get("/ping", ping)
    r.get("/teapot", teapot)
    r.get("/forbidden", forbidden)
    r.get("/boom", boom)
    return r


@pytest.fixture()
def app(routes):
    lifecycle.reset()
    return get_app(routes=routes, config={"APP_DEBUG": True})


@pytest.fixture()
async def client(app):
    import httpx
    from asgi_lifespan import LifespanManager

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_unknown_route_html_gets_styled_404(client):
    resp = await client.get("/definitely-not-here", headers={"Accept": "text/html"})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")
    assert "<html lang=" in resp.text
    assert "<title>" in resp.text and "404" in resp.text


async def test_unknown_route_json_contract_unchanged(client):
    resp = await client.get("/definitely-not-here", headers={"Accept": "application/json"})
    assert resp.status_code == 404
    assert resp.json() == {"message": "Not Found"}


async def test_bridge_header_still_gets_json(client):
    resp = await client.get(
        "/definitely-not-here",
        headers={"Accept": "text/html", "X-Fastplace-Request": "true"},
    )
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"message": "Not Found"}


async def test_http_exception_status_and_detail_reach_the_page(client):
    resp = await client.get("/teapot", headers={"Accept": "text/html"})
    assert resp.status_code == 418
    assert "teapot" in resp.text
    # a11y1-G3: titled, lang-attributed document — no bare JSON blob.
    assert "418" in resp.text.split("</title>")[0]


async def test_http_exception_headers_preserved_on_both_paths(client):
    html_resp = await client.get("/teapot", headers={"Accept": "text/html"})
    assert html_resp.headers.get("retry-after") == "7"
    json_resp = await client.get("/teapot", headers={"Accept": "application/json"})
    assert json_resp.headers.get("retry-after") == "7"


async def test_debug_lists_registered_routes_on_the_page(client):
    resp = await client.get("/definitely-not-here", headers={"Accept": "text/html"})
    assert "/ping" in resp.text  # debug orientation card (supp-2-G10)


async def test_production_page_has_no_route_listing(routes):
    lifecycle.reset()
    app = get_app(routes=routes, config={"APP_DEBUG": False})
    import httpx

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/definitely-not-here", headers={"Accept": "text/html"})
    assert resp.status_code == 404
    assert "/ping" not in resp.text


async def test_app_override_page_wins(routes, tmp_path, monkeypatch):
    (tmp_path / "public" / "errors").mkdir(parents=True)
    (tmp_path / "public" / "errors" / "404.html").write_text(
        "<!doctype html><html lang='en'><title>gone fishing</title></html>"
    )
    monkeypatch.chdir(tmp_path)
    lifecycle.reset()
    app = get_app(routes=routes, config={"APP_DEBUG": False}, project_root=tmp_path)
    import httpx

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/definitely-not-here", headers={"Accept": "text/html"})
    assert resp.status_code == 404
    assert "gone fishing" in resp.text


async def test_fastplace_error_html_gets_styled_page(client):
    """Domain errors share the browser contract — HTML page, not JSON blob."""
    html_resp = await client.get("/forbidden", headers={"Accept": "text/html"})
    assert html_resp.status_code == 403
    assert html_resp.headers["content-type"].startswith("text/html")
    assert "not allowed" in html_resp.text
    assert "403" in html_resp.text.split("</title>")[0]

    json_resp = await client.get("/forbidden", headers={"Accept": "application/json"})
    assert json_resp.status_code == 403
    assert json_resp.json() == {"message": "not allowed"}


async def test_fastplace_error_override_page_wins(routes, tmp_path, monkeypatch):
    (tmp_path / "public" / "errors").mkdir(parents=True)
    (tmp_path / "public" / "errors" / "403.html").write_text(
        "<!doctype html><html lang='en'><title>denied override</title></html>"
    )
    monkeypatch.chdir(tmp_path)
    lifecycle.reset()
    app = get_app(routes=routes, config={"APP_DEBUG": False}, project_root=tmp_path)
    import httpx

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/forbidden", headers={"Accept": "text/html"})
    assert resp.status_code == 403
    assert "denied override" in resp.text


async def test_unhandled_exception_500_override_wins_even_in_debug(routes, tmp_path, monkeypatch):
    """public/errors/500.html outranks both debug and production pages.

    A branded 500 page is app content, not debug output — developers who
    shipped one expect it in every mode.
    """
    (tmp_path / "public" / "errors").mkdir(parents=True)
    (tmp_path / "public" / "errors" / "500.html").write_text(
        "<!doctype html><html lang='en'><title>our bad (override)</title></html>"
    )
    monkeypatch.chdir(tmp_path)
    lifecycle.reset()
    app = get_app(routes=routes, config={"APP_DEBUG": True}, project_root=tmp_path)
    import httpx

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/boom", headers={"Accept": "text/html"})
    assert resp.status_code == 500
    assert "our bad (override)" in resp.text
    assert "RuntimeError" not in resp.text
