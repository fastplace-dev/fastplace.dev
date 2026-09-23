"""Shared auth page props — scope stash, sync callback, registration (§4.16)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx

from fastplace.authz import gate
from fastplace.http.middleware import Middleware


def make_request(user=None, scope=None):
    # The callback only touches .user and .scope — a stub is enough.
    return SimpleNamespace(user=user, scope=scope if scope is not None else {})


class Member:
    def to_dict(self):
        return {"id": 7, "email": "member@example.com"}


class TestSharedAuthPropsCallback:
    def test_guest_gets_none_user_and_empty_can(self):
        from fastplace.auth.middleware import AUTH_CAN_SCOPE, shared_auth_props

        request = make_request(user=None, scope={AUTH_CAN_SCOPE: {}})
        assert shared_auth_props(request) == {"auth": {"user": None, "can": {}}}

    def test_user_serialized_through_to_dict(self):
        from fastplace.auth.middleware import AUTH_CAN_SCOPE, shared_auth_props

        request = make_request(user=Member(), scope={AUTH_CAN_SCOPE: {"ship": True}})
        props = shared_auth_props(request)
        assert props == {
            "auth": {"user": {"id": 7, "email": "member@example.com"}, "can": {"ship": True}}
        }

    def test_missing_scope_degrades_to_empty_can(self):
        # A request that never crossed the middleware (edge setups) still
        # renders — can is simply empty, never a crash.
        from fastplace.auth.middleware import shared_auth_props

        props = shared_auth_props(make_request(user=None, scope={}))
        assert props == {"auth": {"user": None, "can": {}}}


class TestSharedAbilitiesMiddleware:
    async def test_precomputes_configured_abilities_into_scope(self, monkeypatch):
        import fastplace.auth.middleware as auth_mw

        @gate.define("view-dashboard")
        async def view_dashboard(user, *args):
            return user is not None

        @gate.define("reboot")
        async def reboot(user, *args):
            return False

        monkeypatch.setattr(
            auth_mw, "config", lambda key, default=None: ["view-dashboard", "reboot"]
        )
        captured: dict = {}

        async def call_next(request):
            captured.update(request.scope)
            return "response"

        middleware = auth_mw.SharedAbilitiesMiddleware()
        request = make_request(user=Member(), scope={})
        assert isinstance(middleware, Middleware)
        result = await middleware.handle(request, call_next)
        assert result == "response"
        assert captured[auth_mw.AUTH_CAN_SCOPE] == {"view-dashboard": True, "reboot": False}

    async def test_guest_included_before_may_allow(self, monkeypatch):
        import fastplace.auth.middleware as auth_mw

        @gate.before
        async def public_reads(user, ability, *args):
            if ability.startswith("view-"):
                return True
            return None

        monkeypatch.setattr(auth_mw, "config", lambda key, default=None: ["view-post"])
        captured: dict = {}

        async def call_next(request):
            captured.update(request.scope)
            return None

        await auth_mw.SharedAbilitiesMiddleware().handle(
            make_request(user=None, scope={}), call_next
        )
        assert captured[auth_mw.AUTH_CAN_SCOPE] == {"view-post": True}

    async def test_empty_list_stashes_empty_map_without_gate_calls(self, monkeypatch):
        import fastplace.auth.middleware as auth_mw

        monkeypatch.setattr(auth_mw, "config", lambda key, default=None: [])
        captured: dict = {}

        async def call_next(request):
            captured.update(request.scope)
            return None

        await auth_mw.SharedAbilitiesMiddleware().handle(
            make_request(user=None, scope={}), call_next
        )
        assert captured[auth_mw.AUTH_CAN_SCOPE] == {}

    async def test_env_string_form_is_split_on_commas(self, monkeypatch):
        import fastplace.auth.middleware as auth_mw

        @gate.define("a")
        async def a(user, *args):
            return True

        @gate.define("b")
        async def b(user, *args):
            return True

        # .env values arrive as raw strings — the loader does not split lists.
        monkeypatch.setattr(auth_mw, "config", lambda key, default=None: "a, b")
        captured: dict = {}

        async def call_next(request):
            captured.update(request.scope)
            return None

        await auth_mw.SharedAbilitiesMiddleware().handle(
            make_request(user=None, scope={}), call_next
        )
        assert captured[auth_mw.AUTH_CAN_SCOPE] == {"a": True, "b": True}


class TestRegistrationAndMerge:
    async def test_get_app_page_carries_auth_when_registered(self, monkeypatch):
        # Pins the wiring through the REAL get_app: config/app.py MIDDLEWARE
        # entry + sync callback + page_payload merge.
        from pathlib import Path

        import fastplace.auth.middleware as auth_mw
        from fastplace.http import render as render_fn
        from fastplace.http.kernel import _middleware_from_config, get_app
        from fastplace.http.render import share
        from fastplace.http.router import Router

        @gate.define("view-dashboard")
        async def view_dashboard(user, *args):
            return True

        monkeypatch.setattr(auth_mw, "config", lambda key, default=None: ["view-dashboard"])
        share(auth_mw.shared_auth_props)

        async def page(request):
            return render_fn(request, component="Dashboard/Index", props={"n": 3})

        router = Router()
        router.get("/dashboard", page)
        # The config-declared stack (config/app.py MIDDLEWARE) — get_app
        # itself installs no middleware unless asked, and the can map only
        # exists when SharedAbilitiesMiddleware actually runs.
        app = get_app(
            routes=router,
            middleware=_middleware_from_config(Path.cwd()),
            config={"APP_ENV": "local", "APP_KEY": "authz-t5"},
        )
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/dashboard", headers={"X-Fastplace-Request": "true"})
        body = response.json()
        assert response.status_code == 200
        assert body["props"]["n"] == 3
        assert body["props"]["auth"] == {
            "user": None,
            "can": {"view-dashboard": True},
        }

    async def test_page_props_override_shared_auth(self, monkeypatch):
        # setdefault semantics: a controller-supplied auth key always wins.
        import fastplace.auth.middleware as auth_mw
        from fastplace.http import render as render_fn
        from fastplace.http.kernel import get_app
        from fastplace.http.render import share
        from fastplace.http.router import Router

        monkeypatch.setattr(auth_mw, "config", lambda key, default=None: [])
        share(auth_mw.shared_auth_props)

        async def page(request):
            return render_fn(
                request,
                component="Dashboard/Index",
                props={"auth": {"user": None, "can": {"custom": True}}},
            )

        router = Router()
        router.get("/dashboard", page)
        app = get_app(routes=router, config={"APP_ENV": "local", "APP_KEY": "authz-t5b"})
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/dashboard", headers={"X-Fastplace-Request": "true"})
        assert response.json()["props"]["auth"] == {"user": None, "can": {"custom": True}}

    def test_share_dedupes_by_identity(self):
        from fastplace.http.render import page_payload, share

        calls: list[int] = []

        def counting_callback(request):
            calls.append(1)
            return {"counted": True}

        share(counting_callback)
        share(counting_callback)  # repeated registration must not stack

        # Mirror the request stub idiom from tests/http/test_shared_props.py.
        request = SimpleNamespace(full_path="/x", session={}, is_bridge=True)
        payload = page_payload(request, component="X", props={})
        assert payload["props"]["counted"] is True
        assert calls == [1]  # invoked exactly once

    async def test_create_app_registers_the_default_share(self, tmp_path, monkeypatch):
        # The real boot path: create_app alone wires shared_auth_props, and a
        # guest bridge GET on a real route sees the auth key.
        import os
        from pathlib import Path

        from fastplace.http.kernel import create_app

        @gate.define("view-dashboard")
        async def view_dashboard(user, *args):
            return True

        # Pin the configured-abilities path against the REAL project config:
        # monkeypatch the middleware's config lookup, not the config module.
        import fastplace.auth.middleware as auth_mw

        monkeypatch.setattr(auth_mw, "config", lambda key, default=None: ["view-dashboard"])

        root = Path.cwd()
        env_before = set(os.environ)
        try:
            app = create_app(root)
        finally:
            for key in set(os.environ) - env_before:
                os.environ.pop(key, None)

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/login", headers={"X-Fastplace-Request": "true"})
        assert response.status_code == 200
        props = response.json()["props"]
        assert props["auth"] == {"user": None, "can": {"view-dashboard": True}}
