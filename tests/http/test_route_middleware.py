"""Per-route middleware: alias resolution, ordering, groups (spec §4.5)."""

from __future__ import annotations

import pytest

from fastplace.errors import ConfigurationError
from fastplace.http.kernel import get_app
from fastplace.http.middleware import Middleware
from fastplace.http.router import Router, resolve_route_middleware


class RecordingMiddleware(Middleware):
    def __init__(self, label: str, trace: list[str]) -> None:
        self.label = label
        self.trace = trace

    async def handle(self, request, call_next):
        self.trace.append(f"before:{self.label}")
        response = await call_next(request)
        self.trace.append(f"after:{self.label}")
        return response


def make_throttle_factory(trace: list[str]):
    """Callable-factory alias: parameterized ``throttle:N,D`` entries."""

    class ThrottleMiddleware(Middleware):
        def __init__(self, *args: str) -> None:
            self.args = args

        async def handle(self, request, call_next):
            trace.append("throttle:" + ",".join(self.args))
            return await call_next(request)

    return ThrottleMiddleware


def build_router(trace: list[str]) -> Router:
    from fastplace.http.response import Json

    async def handler(request):
        trace.append("controller")
        return Json({"item_id": request.path_params.get("item_id")})

    router = Router()
    router.get("/items/{item_id}", handler, middleware=["trace-outer", "trace-inner"])
    return router


def registry_for(trace: list[str], throttle_factory) -> dict:
    return {
        "trace-outer": RecordingMiddleware("outer", trace),
        "trace-inner": RecordingMiddleware("inner", trace),
        "throttle": throttle_factory,
    }


async def test_middleware_runs_outermost_first_around_the_controller():
    trace: list[str] = []
    router = build_router(trace)
    app = get_app(
        routes=router,
        config={"APP_ENV": "local"},
        route_middleware=registry_for(trace, make_throttle_factory(trace)),
    )
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/items/42")
    assert response.status_code == 200
    assert trace == ["before:outer", "before:inner", "controller", "after:inner", "after:outer"]


async def test_parameterized_alias_parses_colon_and_comma_args():
    trace: list[str] = []
    throttle = make_throttle_factory(trace)
    from fastplace.http.response import Json

    async def handler(request):
        return Json({"ok": True})

    router = Router()
    router.post("/login", handler, middleware=["throttle:5,60"])
    app = get_app(
        routes=router,
        config={"APP_ENV": "local"},
        route_middleware={"throttle": throttle},
    )
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/login")
    assert trace == ["throttle:5,60"]


async def test_unknown_alias_fails_at_boot_not_at_request():
    from fastplace.http.response import Json

    async def handler(request):
        return Json({"ok": True})

    router = Router()
    router.get("/x", handler, middleware=["does-not-exist"])
    with pytest.raises(ConfigurationError):
        get_app(routes=router, config={"APP_ENV": "local"})


async def test_instance_alias_rejects_arguments():
    trace: list[str] = []
    with pytest.raises(ConfigurationError):
        resolve_route_middleware({"trace": RecordingMiddleware("x", trace)}, "trace:5")


async def test_group_prefix_and_middleware_apply_to_nested_routes():
    trace: list[str] = []
    throttle = make_throttle_factory(trace)
    from fastplace.http.response import Json

    async def handler(request):
        return Json({"ok": True})

    router = Router()
    with router.group("/admin", middleware=["throttle:1,1"]):
        router.get("/dash", handler)
    assert router.routes[0].path == "/admin/dash"
    assert router.routes[0].middleware == ("throttle:1,1",)

    app = get_app(
        routes=router, config={"APP_ENV": "local"}, route_middleware={"throttle": throttle}
    )
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/admin/dash")
    assert response.status_code == 200
    assert trace == ["throttle:1,1"]


async def test_group_restores_state_after_exit():
    from fastplace.http.response import Json

    async def handler(request):
        return Json({"ok": True})

    router = Router()
    with router.group("/a", middleware=["throttle:1,1"]):
        pass
    router.get("/plain", handler)
    assert router.prefix == ""
    assert router.routes[0].middleware == ()


async def test_path_params_reach_the_controller_through_the_chain():
    trace: list[str] = []
    router = build_router(trace)
    app = get_app(
        routes=router,
        config={"APP_ENV": "local"},
        route_middleware=registry_for(trace, make_throttle_factory(trace)),
    )
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/items/99")
    assert response.json() == {"item_id": "99"}


async def test_routes_without_middleware_keep_working():
    from fastplace.http.response import Json

    async def handler(request):
        return Json({"ok": True})

    router = Router()
    router.get("/plain", handler)
    app = get_app(routes=router, config={"APP_ENV": "local"})
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/plain")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_config_dotted_paths_resolve_into_the_registry(monkeypatch):
    import fastplace.http.middleware as mw_module

    trace: list[str] = []

    class TraceAliasMiddleware(Middleware):
        async def handle(self, request, call_next):
            trace.append("alias")
            return await call_next(request)

    monkeypatch.setattr(mw_module, "TraceAliasMiddleware", TraceAliasMiddleware, raising=False)
    from fastplace.http.kernel import _route_middleware_registry

    registry = _route_middleware_registry(
        None,
        _Shim({"ROUTE_MIDDLEWARE": {"trace": "fastplace.http.middleware.TraceAliasMiddleware"}}),
    )
    built = resolve_route_middleware(registry, "trace")
    assert isinstance(built, TraceAliasMiddleware)


class _Shim:
    """Minimal _ConfigShim stand-in for registry unit tests."""

    def __init__(self, overrides: dict) -> None:
        self.overrides = overrides

    def get(self, key: str, default=None):
        return self.overrides.get(key, default)
