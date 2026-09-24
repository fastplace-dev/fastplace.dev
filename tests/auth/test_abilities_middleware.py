"""abilities:a,b (ALL) / ability:a,b (ANY) route middleware (spec §4.5)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from fastplace.errors import AuthenticationError, AuthorizationError, ConfigurationError
from fastplace.http.kernel import get_app
from fastplace.http.response import Json


class TestAbilitiesAll:
    async def test_wildcard_pat_passes_the_all_check(self):
        from fastplace.auth.middleware import AbilitiesMiddleware

        mw = AbilitiesMiddleware("orders", "posts")
        response = await mw.handle(user_with_abilities(["*"]), call_next_ok())
        assert json.loads(response.body) == {"ok": True}

    async def test_missing_one_ability_denies(self):
        from fastplace.auth.middleware import AbilitiesMiddleware

        mw = AbilitiesMiddleware("orders", "posts")
        with pytest.raises(AuthorizationError):
            await mw.handle(user_with_abilities(["orders"]), call_next_ok())

    async def test_session_user_passes_without_any_marker(self):
        from fastplace.auth.middleware import AbilitiesMiddleware

        mw = AbilitiesMiddleware("orders")
        response = await mw.handle(user_session(), call_next_ok())
        assert json.loads(response.body) == {"ok": True}


class TestAbilityAny:
    async def test_any_single_ability_passes(self):
        from fastplace.auth.middleware import AbilityMiddleware

        mw = AbilityMiddleware("orders", "posts")
        response = await mw.handle(user_with_abilities(["posts"]), call_next_ok())
        assert json.loads(response.body) == {"ok": True}

    async def test_none_of_the_abilities_denies(self):
        from fastplace.auth.middleware import AbilityMiddleware

        mw = AbilityMiddleware("orders", "posts")
        with pytest.raises(AuthorizationError):
            await mw.handle(user_with_abilities(["billing"]), call_next_ok())


class TestAnonymousAndConfig:
    async def test_anonymous_api_request_is_unauthorized(self):
        from fastplace.auth.middleware import AbilitiesMiddleware

        mw = AbilitiesMiddleware("orders")
        request = SimpleNamespace(
            user=None,
            scope={"fastplace_user": None},
            path="/api/whatever",
            is_bridge=False,
        )
        with pytest.raises(AuthenticationError):
            await mw.handle(request, call_next_ok())

    async def test_anonymous_browser_request_redirects_to_login(self):
        from fastplace.auth.middleware import AbilitiesMiddleware

        mw = AbilitiesMiddleware("orders")
        request = SimpleNamespace(
            user=None,
            scope={},
            path="/settings/tokens",
            full_path="/settings/tokens",
            is_bridge=False,
            session={},
        )
        response = await mw.handle(request, call_next_ok())
        assert response.status_code == 302
        assert response.headers["location"] == "/login"
        assert request.session["url.intended"] == "/settings/tokens"

    def test_empty_ability_list_is_a_configuration_error(self):
        from fastplace.auth.middleware import AbilitiesMiddleware, AbilityMiddleware

        with pytest.raises(ConfigurationError):
            AbilitiesMiddleware()
        with pytest.raises(ConfigurationError):
            AbilityMiddleware("")

    def test_config_registers_both_aliases(self):
        # Task 6's routes name these at declaration time; the kernel resolves
        # them eagerly at mount — both must ship in config/app.py now.
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "app_config_under_test", Path("config/app.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert "abilities" in module.ROUTE_MIDDLEWARE
        assert "ability" in module.ROUTE_MIDDLEWARE

    async def test_route_declaring_the_alias_boots_and_guards(self):
        # End-to-end through get_app: alias resolution + the 401 envelope on
        # an anonymous /api request.
        from fastplace.http.router import Router

        class EchoController:
            async def index(self, request):
                return Json({"ok": True})

        router = Router()
        router.get("/api/gated", EchoController, "index", middleware=["ability:orders"])
        app = get_app(routes=router, config={"APP_ENV": "local"}, route_middleware=registry())
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/gated")
        assert response.status_code == 401


def registry() -> dict:
    from fastplace.auth.middleware import AbilitiesMiddleware, AbilityMiddleware

    return {"abilities": AbilitiesMiddleware, "ability": AbilityMiddleware}


def _real_request(scope: dict):
    from fastplace.http.request import Request

    return Request(SimpleNamespace(scope=scope))


def user_with_abilities(abilities: list[str]):
    return _real_request(
        {
            "fastplace_user": SimpleNamespace(id=7, name="Firoz"),
            "fastplace_via_pat": True,
            "fastplace_pat_abilities": abilities,
        }
    )


def user_session():
    return _real_request({"fastplace_user": SimpleNamespace(id=7, name="Firoz")})


def token_with_abilities(abilities: list[str]):
    return user_with_abilities(abilities)


def call_next_ok():
    async def call_next(request):
        return Json({"ok": True})

    return call_next
