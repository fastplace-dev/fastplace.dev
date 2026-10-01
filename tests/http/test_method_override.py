"""No-JS form verb override — the backend half of the bridge Form contract.

The React Form ships ``<input type="hidden" name="_method">`` for put/patch/
delete posts; HTML forms can only ever send POST. The kernel installs
MethodOverrideMiddleware inside the configured stack, so CSRF validates the
real POST first and only then does the router see the intended verb.
"""

from __future__ import annotations

import httpx
import pytest

from fastplace.http.kernel import get_app
from fastplace.http.router import Router

CSRF_HEADER = "X-Fastplace-CSRF-Token"


class ThingController:
    async def _payload(self, request) -> dict:
        content_type = (request.header("Content-Type") or "").lower()
        if "json" in content_type:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        form = await request.form()
        return {key: form.get(key) for key in form.keys()}

    async def create(self, request):
        return {"method": "POST", "name": (await self._payload(request)).get("name")}

    async def update(self, request):
        return {"method": "PUT", "name": (await self._payload(request)).get("name")}

    async def destroy(self, request):
        return {"method": "DELETE"}


class OnlyGetController:
    async def show(self, request):
        return {"method": "GET"}


def _build_app():
    router = Router()
    router.post("/thing", ThingController, "create", name="thing.create")
    router.put("/thing", ThingController, "update", name="thing.update")
    router.delete("/thing", ThingController, "destroy", name="thing.destroy")
    router.get("/only-get", OnlyGetController, "show", name="onlyget.show")
    from fastplace.auth.middleware import CsrfMiddleware

    return get_app(
        routes=router,
        middleware=[CsrfMiddleware()],
        config={"APP_ENV": "local", "APP_KEY": "test-app-key-not-for-production-use-only"},
    )


@pytest.fixture()
async def client():
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _token(client) -> str:
    page = await client.get("/only-get")
    return page.headers[CSRF_HEADER]


async def test_put_override_reaches_put_route_with_params(client):
    token = await _token(client)
    response = await client.post(
        "/thing",
        data={"_method": "PUT", "name": "fastplace"},
        headers={CSRF_HEADER: token},
    )
    assert response.status_code == 200
    assert response.json() == {"method": "PUT", "name": "fastplace"}


async def test_delete_override_reaches_delete_route(client):
    token = await _token(client)
    response = await client.post("/thing", data={"_method": "delete"}, headers={CSRF_HEADER: token})
    assert response.status_code == 200
    assert response.json() == {"method": "DELETE"}


async def test_override_verb_is_case_insensitive(client):
    token = await _token(client)
    response = await client.post(
        "/thing", data={"_method": "Put", "name": "x"}, headers={CSRF_HEADER: token}
    )
    assert response.status_code == 200
    assert response.json() == {"method": "PUT", "name": "x"}


async def test_bogus_verb_stays_post(client):
    token = await _token(client)
    response = await client.post(
        "/thing", data={"_method": "EVIL", "name": "x"}, headers={CSRF_HEADER: token}
    )
    assert response.status_code == 200
    assert response.json() == {"method": "POST", "name": "x"}


async def test_get_verb_is_not_overridable(client):
    # GET through the override would dodge the unsafe-method CSRF gate —
    # the request must stay POST and miss the GET-only route.
    token = await _token(client)
    response = await client.post("/only-get", data={"_method": "GET"}, headers={CSRF_HEADER: token})
    assert response.status_code == 405


async def test_json_method_field_is_ignored(client):
    token = await _token(client)
    response = await client.post(
        "/thing", json={"_method": "PUT", "name": "x"}, headers={CSRF_HEADER: token}
    )
    assert response.status_code == 200
    assert response.json() == {"method": "POST", "name": "x"}


async def test_override_without_csrf_token_is_rejected(client):
    await _token(client)
    response = await client.post("/thing", data={"_method": "PUT", "name": "x"})
    assert response.status_code == 419


async def test_multipart_override_reaches_put_route(client):
    token = await _token(client)
    response = await client.post(
        "/thing",
        data={"_method": "PUT", "name": "from-multipart"},
        files={"doc": ("notes.txt", b"hello", "text/plain")},
        headers={CSRF_HEADER: token},
    )
    assert response.status_code == 200
    assert response.json() == {"method": "PUT", "name": "from-multipart"}
