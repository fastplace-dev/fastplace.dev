"""HTTP kernel, router, request/response, render, middleware, lifecycle tests."""

import pytest
from pydantic import BaseModel

from fastplace.http import (
    Controller,
    Html,
    Json,
    Middleware,
    Request,
    Router,
    get_app,
    lifecycle,
    render,
)

# ---------------------------------------------------------------------------
# Fixtures: build an isolated Fastplace app per test module
# ---------------------------------------------------------------------------


@pytest.fixture()
def routes() -> Router:
    r = Router()

    async def ping(request: Request):
        return {"pong": True}

    async def echo_json(request: Request):
        payload = await request.json()
        return payload

    async def param(request: Request):
        return {"id": request.param("id")}

    async def hello(request: Request):
        return Json({"hello": "world"})

    async def html_page(request: Request):
        return Html("<h1>Hi</h1>")

    async def typed(request: Request) -> SampleOut:
        return SampleOut(name="typed", count=2)

    async def not_found(request: Request):
        from fastplace.errors import NotFoundError

        raise NotFoundError("no such thing")

    async def invalid(request: Request):
        from fastplace.errors import ValidationError

        raise ValidationError("bad input", errors={"title": ["required"]})

    async def boom(request: Request):
        raise RuntimeError("boom")

    r.get("/ping", ping)
    r.post("/echo", echo_json)
    r.get("/items/{id}", param)
    r.get("/hello", hello)
    r.get("/html", html_page)
    r.get("/typed", typed)
    r.get("/missing", not_found)
    r.get("/invalid", invalid)
    r.get("/boom", boom)
    return r


class SampleOut(BaseModel):
    name: str
    count: int


@pytest.fixture()
def app(routes: Router):
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


# ---------------------------------------------------------------------------
# Router + kernel
# ---------------------------------------------------------------------------


async def test_json_dict_response(client):
    resp = await client.get("/ping")
    assert resp.status_code == 200
    assert resp.json() == {"pong": True}


async def test_post_json_body(client):
    resp = await client.post("/echo", json={"a": 1})
    assert resp.json() == {"a": 1}


async def test_path_params_via_request(client):
    resp = await client.get("/items/42")
    assert resp.json() == {"id": "42"}


async def test_helper_responses_pass_through(client):
    assert (await client.get("/hello")).json() == {"hello": "world"}
    assert "<h1>Hi</h1>" in (await client.get("/html")).text


async def test_pydantic_return_annotation_serialized(client):
    data = (await client.get("/typed")).json()
    assert data == {"name": "typed", "count": 2}


async def test_404_for_unknown_route_is_json(client):
    resp = await client.get("/definitely-not-here")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


async def test_domain_not_found_maps_to_404(client):
    resp = await client.get("/missing")
    assert resp.status_code == 404
    assert "no such thing" in resp.json()["message"]


async def test_validation_error_maps_to_422_with_errors(client):
    resp = await client.get("/invalid")
    assert resp.status_code == 422
    body = resp.json()
    assert body["errors"]["title"] == ["required"]


async def test_unhandled_exception_maps_to_500(routes):
    import httpx

    from fastplace.http import get_app

    app = get_app(routes=routes, config={"APP_DEBUG": False})
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/boom")
    assert resp.status_code == 500
    assert resp.json()["message"] == "Server error."


# ---------------------------------------------------------------------------
# render() — the Inertia-style bridge
# ---------------------------------------------------------------------------


async def test_render_initial_load_returns_html_with_payload(app):
    async def page(request: Request):
        return render(request, component="Dashboard/Index", props={"user": {"id": 1}})

    from fastplace.http import Router, get_app

    r = Router()
    r.get("/dashboard", page)
    a = get_app(routes=r)
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="http://test") as c:
        resp = await c.get("/dashboard")
    assert resp.status_code == 200
    assert 'id="fastplace"' in resp.text
    assert "Dashboard/Index" in resp.text
    assert resp.headers["content-type"].startswith("text/html")


async def test_router_accepts_controller_class_and_action():
    """declarative registration: router.get(path, Controller, "action")."""

    class PingController(Controller):
        async def index(self, request: Request):
            return {"controller": "ping"}

    from fastplace.http import Router, get_app

    r = Router()
    r.get("/ctl", PingController, "index", name="ping")
    a = get_app(routes=r)
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="http://test") as c:
        resp = await c.get("/ctl")
    assert resp.status_code == 200
    assert resp.json() == {"controller": "ping"}


async def test_render_bridge_request_returns_json_only(app):
    async def page(request: Request):
        return render(request, component="Dashboard/Index", props={"ok": True})

    from fastplace.http import Router, get_app

    r = Router()
    r.get("/dashboard", page)
    a = get_app(routes=r)
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="http://test") as c:
        resp = await c.get("/dashboard", headers={"X-Fastplace-Request": "true"})
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["component"] == "Dashboard/Index"
    assert body["props"]["ok"] is True
    assert body["url"] == "/dashboard"


async def test_render_accepts_pydantic_props():
    class Props(BaseModel):
        n: int

    from starlette.requests import Request as SRequest

    from fastplace.http.request import Request as FpRequest

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [(b"x-fastplace-request", b"true")],
        "query_string": b"",
    }
    sreq = SRequest(scope)
    resp = render(FpRequest(sreq), component="A/B", props=Props(n=3))
    import json

    body = json.loads(resp.body)
    assert body["props"] == {"n": 3}


# ---------------------------------------------------------------------------
# Middleware & lifecycle
# ---------------------------------------------------------------------------


class TraceMiddleware(Middleware):
    async def handle(self, request, call_next):
        order.append("trace-before")
        resp = await call_next(request)
        order.append("trace-after")
        return resp


order: list[str] = []


class HeaderMiddleware(Middleware):
    async def handle(self, request, call_next):
        resp = await call_next(request)
        resp.headers["X-Trace"] = "yes"
        return resp


async def test_middleware_runs_and_can_mutate_response():
    from fastplace.http import Router, get_app

    order.clear()
    r = Router()

    async def ping(request):
        return {"ok": True}

    r.get("/ping", ping)
    a = get_app(routes=r, middleware=[TraceMiddleware(), HeaderMiddleware()])
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="http://test") as c:
        resp = await client_get(c)
    assert order == ["trace-before", "trace-after"]
    assert resp.headers["x-trace"] == "yes"


async def client_get(c):
    return await c.get("/ping")


async def test_lifecycle_hooks_fire():
    from asgi_lifespan import LifespanManager

    from fastplace.http import Router, get_app

    lifecycle.reset()
    fired = []
    lifecycle.on_startup(lambda: fired.append("up") or None)
    lifecycle.on_shutdown(lambda: fired.append("down") or None)
    a = get_app(routes=Router())
    async with LifespanManager(a):
        pass
    assert fired == ["up", "down"]
    lifecycle.reset()


# ---------------------------------------------------------------------------
# Misc surface
# ---------------------------------------------------------------------------


def test_controller_base_supports_subclassing():
    class Sub(Controller):
        pass

    assert isinstance(Sub(), Controller)


async def test_request_surface(app):
    from fastplace.http import Router, get_app

    async def probe(request: Request):
        return {
            "method": request.method,
            "path": request.path,
            "query": dict(request.query_params),
            "header": request.header("x-custom"),
            "cookie": request.cookie("snack"),
            "ip": request.ip,
        }

    r = Router()
    r.get("/probe", probe)
    a = get_app(routes=r)
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="http://test") as c:
        resp = await c.get(
            "/probe?a=1",
            headers={"x-custom": "cv", "cookie": "snack=biscuit"},
        )
    data = resp.json()
    assert data["method"] == "GET"
    assert data["path"] == "/probe"
    assert data["query"] == {"a": "1"}
    assert data["header"] == "cv"
    assert data["cookie"] == "biscuit"


class TestKernelHardening:
    async def test_declared_first_middleware_is_outermost(self):
        # Starlette's add_middleware inserts at index 0 — the kernel must
        # register in reverse so the documented "first declared = outermost"
        # contract actually holds (auth resolution must run before CSRF).
        from fastplace.http import Router, get_app
        from fastplace.http.middleware import Middleware

        seen: list[str] = []

        class Outer(Middleware):
            async def handle(self, request, call_next):
                seen.append("outer-before")
                response = await call_next(request)
                seen.append("outer-after")
                return response

        class Inner(Middleware):
            async def handle(self, request, call_next):
                seen.append("inner-before")
                return await call_next(request)

        async def ping(request):
            return {"ok": True}

        r = Router()
        r.get("/ping", ping)
        app = get_app(
            routes=r,
            middleware=[Outer(), Inner()],
            config={"APP_ENV": "local", "APP_KEY": "test-app-key-not-for-production-use-only"},
        )
        import httpx

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            await c.get("/ping")
        assert seen == ["outer-before", "inner-before", "outer-after"]

    async def test_production_requires_an_app_key(self):
        # An ephemeral per-process key silently invalidates sessions across
        # workers — production must fail fast instead.
        import pytest

        from fastplace.errors import ConfigurationError
        from fastplace.http import Router, get_app

        async def ping(request):
            return {"ok": True}

        r = Router()
        r.get("/ping", ping)
        with pytest.raises(ConfigurationError, match="APP_KEY"):
            get_app(routes=r, config={"APP_ENV": "production", "APP_KEY": ""})

    async def test_local_dev_allows_an_ephemeral_app_key(self):
        from fastplace.http import Router, get_app

        async def ping(request):
            return {"ok": True}

        r = Router()
        r.get("/ping", ping)
        app = get_app(routes=r, config={"APP_ENV": "local", "APP_KEY": ""})
        assert app is not None
