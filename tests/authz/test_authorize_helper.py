"""The authorize() controller helper (spec §4.15)."""

from __future__ import annotations

import httpx

from fastplace.authz import gate
from fastplace.http import authorize
from fastplace.http.kernel import get_app
from fastplace.http.router import Router

CONFIG = {"APP_ENV": "local", "APP_KEY": "authz-t4"}


async def controller(request):
    await authorize(request, "update-project", request.path_params["project"])
    return {"updated": request.path_params["project"]}


async def open_controller(request):
    await authorize(request, "enter")
    return {"ok": True}


class TestAuthorizeHelper:
    async def test_allow_passes_args_through(self):
        @gate.define("update-project")
        async def update_project(user, project):
            return project == "mine"

        router = Router()
        router.get("/projects/{project}", controller)
        app = get_app(routes=router, config=CONFIG)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ok = await client.get("/projects/mine")
            denied = await client.get("/projects/yours")
        assert ok.status_code == 200
        assert ok.json() == {"updated": "mine"}
        assert denied.status_code == 403
        assert denied.json() == {"message": "This action is unauthorized."}

    async def test_guest_denies_with_403(self):
        @gate.define("enter")
        async def enter(user, *args):
            return user is not None

        router = Router()
        router.get("/open", open_controller)
        app = get_app(routes=router, config=CONFIG)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/open")
        assert response.status_code == 403
