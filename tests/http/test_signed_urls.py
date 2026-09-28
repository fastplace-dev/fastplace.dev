"""Signed URLs — ``signed_url`` builder + the built-in ``signed`` middleware.

``signed_url(path)`` appends ``?signature=...&expires=...`` over the route
path via ``fastplace.auth.signing``; the ``signed`` route middleware
validates those params against the request path, so routes opt in with
``middleware=["signed"]`` and anything tampered, stale, or unsigned is a 403.
"""

import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from fastplace.auth.signing import sign, verify
from fastplace.http.kernel import get_app
from fastplace.http.router import Router
from fastplace.http.urls import signed_url


@pytest.fixture(autouse=True)
def _signing_env(monkeypatch):
    monkeypatch.setenv("APP_KEY", "w9-signed-url-secret")
    monkeypatch.setenv("APP_URL", "https://app.test")


def _client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="https://app.test")


def test_signed_url_appends_signature_and_expiry():
    url = signed_url("/download/report")
    assert url.startswith("https://app.test/download/report?")
    query = parse_qs(urlparse(url).query)
    assert verify("/download/report", query["signature"][0], query["expires"][0])


def test_ttl_controls_the_expiry():
    url = signed_url("/download/report", ttl=60)
    expires = int(parse_qs(urlparse(url).query)["expires"][0])
    assert abs(expires - (time.time() + 60)) <= 5


async def test_signed_middleware_admits_a_valid_link():
    async def download(request):
        from fastplace.http.response import Json

        return Json({"file": request.path_params["file"]})

    router = Router()
    router.get("/download/{file}", download, middleware=["signed"])
    app = get_app(routes=router)
    async with _client(app) as client:
        response = await client.get(signed_url("/download/report"))
    assert response.status_code == 200
    assert response.json() == {"file": "report"}


async def test_signed_middleware_rejects_tampered_unsigned_and_expired():
    async def download(request):
        from fastplace.http.response import Json

        return Json({"file": request.path_params["file"]})

    router = Router()
    router.get("/download/{file}", download, middleware=["signed"])
    app = get_app(routes=router)
    good_query = parse_qs(urlparse(signed_url("/download/report")).query)
    expired_sig, expired_exp = sign("/download/report", ttl=-10)
    async with _client(app) as client:
        # A valid signature for one path must not open a sibling path.
        tampered = await client.get(
            "/download/other"
            f"?signature={good_query['signature'][0]}&expires={good_query['expires'][0]}"
        )
        unsigned = await client.get("/download/report")
        stale = await client.get(f"/download/report?signature={expired_sig}&expires={expired_exp}")
    assert tampered.status_code == 403
    assert unsigned.status_code == 403
    assert stale.status_code == 403
