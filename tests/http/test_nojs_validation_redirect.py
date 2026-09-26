"""No-JS form posts get redirect-back validation; SPA/API clients keep 422 JSON."""

from __future__ import annotations

import html as html_module
import json

import pytest
from pydantic import BaseModel, Field

from fastplace.http import Request, Router, get_app, lifecycle, render


class FormIn(BaseModel):
    name: str = Field(min_length=3)


@pytest.fixture()
def routes() -> Router:
    r = Router()

    async def form_page(request: Request):
        return render(request, component="Form/Index", props={})

    async def submit(request: Request):
        await request.validate(FormIn)
        return {"ok": True}

    async def search(request: Request):
        await request.validate(FormIn)
        return {"ok": True}

    r.get("/form", form_page)
    r.post("/submit", submit)
    r.get("/search", search)
    return r


@pytest.fixture()
def app(routes: Router):
    lifecycle.reset()
    return get_app(routes=routes, config={"APP_ENV": "local"})


@pytest.fixture()
async def client(app):
    import httpx
    from asgi_lifespan import LifespanManager

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def _page_payload(document: str) -> dict:
    marker = 'data-page="'
    start = document.index(marker) + len(marker)
    end = document.index('"', start)
    return json.loads(html_module.unescape(document[start:end]))


async def test_nojs_form_post_redirects_back_with_flashed_errors(client):
    response = await client.post(
        "/submit",
        content="name=ab",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml",
            "Referer": "http://test/form",
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == "http://test/form"

    # The redirect target re-renders with the flashed field errors once.
    page = await client.get("/form", headers={"Accept": "text/html"})
    payload = _page_payload(page.text)
    assert payload["props"]["errors"] == {"name": ["The name must be at least 3 characters."]}
    again = await client.get("/form", headers={"Accept": "text/html"})
    assert "errors" not in _page_payload(again.text)["props"]


async def test_nojs_post_without_referer_falls_back_to_request_path(client):
    response = await client.post(
        "/submit",
        content="name=ab",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html",
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/submit"


async def test_nojs_post_rejects_cross_site_referer(client):
    response = await client.post(
        "/submit",
        content="name=ab",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html",
            "Referer": "http://evil.example/form",
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/submit"


async def test_bridge_post_keeps_the_422_envelope(client):
    response = await client.post(
        "/submit",
        content="name=ab",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Fastplace-Request": "true",
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["message"] == "The given data was invalid."
    assert body["errors"] == {"name": ["The name must be at least 3 characters."]}


async def test_json_client_keeps_the_422_envelope(client):
    response = await client.post(
        "/submit",
        json={"name": "ab"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["errors"] == {"name": ["The name must be at least 3 characters."]}


async def test_browser_get_never_redirects_back(client):
    """Only unsafe methods redirect back — a failing GET keeps the 422 JSON."""
    response = await client.get(
        "/search",
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 422
    assert response.json()["errors"] == {"name": ["The name field is required."]}
