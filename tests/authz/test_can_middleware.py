"""can: route middleware — alias resolution, guests, params, ordering."""

from __future__ import annotations

import httpx
import pytest

from fastplace.authz import Response, gate
from fastplace.http.kernel import get_app
from fastplace.http.router import Router

CONFIG = {"APP_ENV": "local", "APP_KEY": "authz-t3"}


def build_client(router: Router) -> httpx.AsyncClient:
    # No route_middleware overrides: the routes below resolve their aliases
    # against the REAL config registry — pinning the config/app.py entry.
    app = get_app(routes=router, config=CONFIG)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def dashboard(request):
    return {"ok": True}


async def projects_show(request):
    return {"ok": True}


async def account(request):
    return {"ok": True}


BRIDGE = {"X-Fastplace-Request": "true"}


class TestCanMiddleware:
    async def test_allowed_request_passes_through(self):
        @gate.define("view-dashboard")
        async def view_dashboard(user, *args):
            return True

        router = Router()
        router.get("/dashboard", dashboard, middleware=["can:view-dashboard"])
        async with build_client(router) as client:
            response = await client.get("/dashboard", headers=BRIDGE)
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    async def test_deny_answers_403_json_with_the_message(self):
        @gate.define("view-dashboard")
        async def view_dashboard(user, *args):
            return Response.deny("Members only.")

        router = Router()
        router.get("/dashboard", dashboard, middleware=["can:view-dashboard"])
        async with build_client(router) as client:
            response = await client.get("/dashboard", headers=BRIDGE)
        assert response.status_code == 403
        assert response.json() == {"message": "Members only."}

    async def test_guest_gets_403_json_never_a_redirect(self):
        @gate.define("view-dashboard")
        async def view_dashboard(user, *args):
            return user is not None

        router = Router()
        router.get("/dashboard", dashboard, middleware=["can:view-dashboard"])
        async with build_client(router) as client:
            response = await client.get("/dashboard", follow_redirects=False)
        assert response.status_code == 403
        assert response.json() == {"message": "This action is unauthorized."}

    async def test_deny_as_not_found_answers_404(self):
        @gate.define("view-dashboard")
        async def view_dashboard(user, *args):
            return Response.deny_as_not_found()

        router = Router()
        router.get("/dashboard", dashboard, middleware=["can:view-dashboard"])
        async with build_client(router) as client:
            response = await client.get("/dashboard")
        assert response.status_code == 404
        assert response.json() == {"message": "Resource not found."}

    async def test_param_name_passes_the_raw_string_route_param(self):
        seen: list[object] = []

        @gate.define("update-project")
        async def update_project(user, project):
            seen.append(project)
            return True

        router = Router()
        router.get(
            "/projects/{project}",
            projects_show,
            middleware=["can:update-project,project"],
        )
        async with build_client(router) as client:
            response = await client.get("/projects/proj-42", headers=BRIDGE)
        assert response.status_code == 200
        assert seen == ["proj-42"]  # raw STRING param, never coerced

    async def test_declared_param_missing_from_the_path_is_a_configuration_error(self):
        @gate.define("update-project")
        async def update_project(user, project):
            return True

        router = Router()
        # The route has no {project} param but the alias names one — loud
        # developer error, not a silent deny.
        router.get("/projects", projects_show, middleware=["can:update-project,project"])
        async with build_client(router) as client:
            response = await client.get("/projects")
        assert response.status_code == 500
        assert "project" in response.json()["message"]

    async def test_unknown_alias_still_fails_at_mount_time(self):
        from fastplace.errors import ConfigurationError

        router = Router()
        router.get("/x", dashboard, middleware=["can:view-dashboard"])  # alias exists
        router.get("/y", dashboard, middleware=["not-an-alias:x"])
        with pytest.raises(ConfigurationError, match="not-an-alias"):
            get_app(routes=router, config=CONFIG)


class TestOrderingContract:
    async def test_auth_runs_before_can_unauthenticated_gets_401(self):
        # §9: declaration order is execution order. With ["auth", "can:..."]
        # an unauthenticated bridge call is answered by auth (401) — can is
        # never reached, so its 403 cannot mask the authentication failure.
        @gate.define("view-dashboard")
        async def view_dashboard(user, *args):
            return user is not None

        router = Router()
        router.get("/account", account, middleware=["auth", "can:view-dashboard"])
        async with build_client(router) as client:
            response = await client.get("/account", headers=BRIDGE)
        assert response.status_code == 401
        assert response.json() == {"message": "Unauthenticated."}

    async def test_can_without_auth_still_denies_guests_deterministically(self):
        # can: alone (no auth in front) must deny, not crash, on user=None.
        @gate.define("view-dashboard")
        async def view_dashboard(user, *args):
            return user is not None

        router = Router()
        router.get("/account", account, middleware=["can:view-dashboard"])
        async with build_client(router) as client:
            response = await client.get("/account", headers=BRIDGE)
        assert response.status_code == 403
