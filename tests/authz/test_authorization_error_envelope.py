"""The kernel renders AuthorizationError with its instance status/code."""

from __future__ import annotations

import httpx

from fastplace.errors import AuthorizationError, NotFoundError
from fastplace.http.kernel import get_app
from fastplace.http.router import Router

CONFIG = {"APP_ENV": "local", "APP_KEY": "authz-t1"}


def build_client(routes):
    router = Router()
    for path, handler in routes:
        router.get(path, handler)
    app = get_app(routes=router, config=CONFIG)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def deny(request):
    raise AuthorizationError("You do not own this project.")


async def masked(request):
    raise AuthorizationError("Resource not found.", status_code=404)


async def coded(request):
    raise AuthorizationError("Locked", status_code=423, code="project_locked")


async def real_missing(request):
    raise NotFoundError()


class TestEnvelope:
    async def test_default_deny_is_403_with_the_message(self):
        async with build_client([("/deny", deny)]) as client:
            response = await client.get("/deny", headers={"X-Fastplace-Request": "true"})
        assert response.status_code == 403
        assert response.json() == {"message": "You do not own this project."}

    async def test_instance_status_404_is_indistinguishable_from_not_found(self):
        routes = [("/masked", masked), ("/real", real_missing)]
        async with build_client(routes) as client:
            masked_response = await client.get("/masked")
            real_response = await client.get("/real")
        assert masked_response.status_code == 404
        expected = {"message": "Resource not found."}
        assert masked_response.json() == real_response.json() == expected

    async def test_code_is_emitted_only_when_set(self):
        routes = [("/coded", coded), ("/deny", deny)]
        async with build_client(routes) as client:
            coded_response = await client.get("/coded")
            plain_response = await client.get("/deny")
        assert coded_response.status_code == 423
        assert coded_response.json() == {"message": "Locked", "code": "project_locked"}
        assert "code" not in plain_response.json()
