"""Capture — the route is fetched through the real ASGI stack."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import RedirectResponse
from starlette.routing import Route

from fastplace.prerender.capture import capture_all, capture_route


class MarkerMiddleware:
    """Pure-ASGI middleware stamping a request header the route echoes back.

    If the capture engine bypassed middleware, the marker would never reach
    the route and the body assertions below would fail.
    """

    def __init__(self, app):  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] == "http":
            headers = list(scope.get("headers", []))
            headers.append((b"x-middleware-ran", b"yes"))
            scope["headers"] = headers
        await self.app(scope, receive, send)


async def _echo(request):  # noqa: ANN001
    marker = request.headers.get("x-middleware-ran", "no")
    accept = request.headers.get("accept", "none")
    from fastplace.http import Html

    return Html(f"middleware={marker} accept={accept}")


async def _redirect(request):  # noqa: ANN001
    return RedirectResponse("/target", status_code=301)


async def _startup_only(request):  # noqa: ANN001
    state = getattr(request.app.state, "booted", "never-booted")
    from fastplace.http import Html

    return Html(f"booted={state}")


def _test_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/echo", _echo),
            Route("/redirecting-route", _redirect),
            Route("/boot", _startup_only),
        ]
    )


def _wrapped_app() -> MarkerMiddleware:
    return MarkerMiddleware(_test_app())


async def test_capture_runs_middleware_and_returns_body():
    page = await capture_route(_wrapped_app(), "/echo")
    assert page.status == 200
    assert page.route == "/echo"
    assert b"middleware=yes" in page.body
    # The capture request identifies as a browser navigation.
    assert b"accept=text/html" in page.body
    assert page.content_type.startswith("text/html")


async def test_capture_does_not_follow_redirects():
    page = await capture_route(_wrapped_app(), "/redirecting-route")
    assert page.status == 301


async def test_capture_all_is_ordered():
    pages = await capture_all(_test_app(), ["/b-missing", "/a-missing"])
    # Non-200s are facts, not failures — order preserved as given.
    assert [p.route for p in pages] == ["/b-missing", "/a-missing"]
    assert [p.status for p in pages] == [404, 404]


async def test_capture_all_runs_app_lifespan():
    """Pages rendered by `serve` see lifespan startup state; capture must too.

    Without running the ASGI lifespan, the startup hook never fires and the
    captured HTML drifts from what the live server returns.
    """

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(app):  # noqa: ANN001
        app.state.booted = "yes"
        yield

    app = Starlette(routes=[Route("/boot", _startup_only)], lifespan=_lifespan)
    pages = await capture_all(app, ["/boot"])
    assert pages[0].status == 200
    assert b"booted=yes" in pages[0].body


async def test_capture_all_shuts_lifespan_down():
    """The engine leaves no running lifespan behind after capturing."""
    from contextlib import asynccontextmanager

    events: list[str] = []

    @asynccontextmanager
    async def _lifespan(app):  # noqa: ANN001
        events.append("startup")
        try:
            yield
        finally:
            events.append("shutdown")

    app = Starlette(routes=[Route("/boot", _startup_only)], lifespan=_lifespan)
    await capture_all(app, ["/boot"])
    assert events == ["startup", "shutdown"]


async def test_app_dying_during_lifespan_raises_instead_of_hanging():
    """A lifespan-less app that just exits must fail the capture, not hang.

    The engine races the app coroutine against the handshake; an app that
    dies without a protocol message surfaces its exit as an error. The
    test-side wait_for keeps a regression observable as a failure rather
    than a hung suite.
    """
    import asyncio

    async def _broken_app(scope, receive, send):  # noqa: ANN001
        if scope["type"] == "lifespan":
            raise RuntimeError("no lifespan support here")
        raise RuntimeError("unreachable in this test")

    with pytest.raises((RuntimeError, TimeoutError)) as excinfo:
        await asyncio.wait_for(capture_all(_broken_app, ["/x"]), timeout=5)
    assert not isinstance(excinfo.value, TimeoutError), "capture hung on dead app"


async def test_slow_route_hits_the_capture_timeout():
    """A route slower than the timeout fails the capture, not the evening.

    httpx's default timeout is 5s per phase; a page that hangs (bad DB
    pool, blocked template) would hold `fastplace prerender` for that
    long per route with no knob to turn. The engine takes an explicit
    timeout so the CLI can own one number for the whole run.
    """
    import asyncio

    import httpx

    async def _slow(request):  # noqa: ANN001
        await asyncio.sleep(2)
        from fastplace.http import Html

        return Html("late")

    app = Starlette(routes=[Route("/slow", _slow)])
    with pytest.raises(httpx.TimeoutException):
        await capture_all(app, ["/slow"], timeout=0.1)


async def test_default_timeout_is_generous_for_slow_pages():
    """Sans explicit timeout a 0.3s page still captures (default ~30s).

    Guards against the knob tightening the default: a normal-but-slow
    page must never start timing out because the parameter exists.
    """
    import asyncio

    async def _sluggish(request):  # noqa: ANN001
        await asyncio.sleep(0.3)
        from fastplace.http import Html

        return Html("made it")

    app = Starlette(routes=[Route("/sluggish", _sluggish)])
    pages = await capture_all(app, ["/sluggish"])
    assert pages[0].status == 200
    assert b"made it" in pages[0].body


async def test_shutdown_failure_does_not_mask_the_capture_error():
    """When a capture fails AND shutdown then fails, the capture error wins.

    The lifespan wrapper runs best-effort shutdown while the real error is
    already propagating; surfacing the shutdown failure instead sends the
    developer chasing a lifecycle bug instead of the slow route.
    """
    import asyncio

    import httpx

    async def _moody_app(scope, receive, send):  # noqa: ANN001
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.failed", "message": "shutdown boom"})
                    return
        elif scope["type"] == "http":
            await asyncio.sleep(5)

    with pytest.raises(httpx.TimeoutException, match="capturing /slow"):
        await capture_all(_moody_app, ["/slow"], timeout=0.1)


async def test_startup_failure_raises_with_app_message():
    """A failing startup hook surfaces its message through the engine."""

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(app):  # noqa: ANN001
        raise RuntimeError("db unreachable")
        yield  # pragma: no cover

    app = Starlette(routes=[Route("/boot", _startup_only)], lifespan=_lifespan)
    with pytest.raises(RuntimeError):
        await capture_all(app, ["/boot"])


async def test_lifespan_handshake_timeout_is_a_named_error(monkeypatch):
    """A boot slower than the handshake budget must not stringify to "".

    Bare TimeoutError has an empty message — the CLI printed a message-less
    "prerender failed:" line and the developer could not tell a slow boot
    (model loads, migrations) from a hung route, or learn the budget. The
    error names the phase, the seconds, and that --timeout does not govern it.
    """
    import asyncio
    from contextlib import asynccontextmanager

    import fastplace.prerender.capture as capture_mod

    monkeypatch.setattr(capture_mod, "LIFESPAN_HANDSHAKE_TIMEOUT", 0.05)

    @asynccontextmanager
    async def _lifespan(app):  # noqa: ANN001
        await asyncio.sleep(1)
        yield  # pragma: no cover

    app = Starlette(routes=[Route("/boot", _startup_only)], lifespan=_lifespan)
    with pytest.raises(RuntimeError) as excinfo:
        await asyncio.wait_for(capture_all(app, ["/boot"]), timeout=5)
    message = str(excinfo.value)
    assert "lifespan startup timed out after 0.05s" in message
    assert "--timeout" in message  # the two budgets are named apart


async def test_lifespan_shutdown_timeout_is_a_named_error(monkeypatch):
    """The shutdown handshake gets the same named error (it shared the bare
    TimeoutError shape)."""
    import asyncio
    from contextlib import asynccontextmanager

    import fastplace.prerender.capture as capture_mod

    monkeypatch.setattr(capture_mod, "LIFESPAN_HANDSHAKE_TIMEOUT", 0.05)

    @asynccontextmanager
    async def _lifespan(app):  # noqa: ANN001
        yield
        await asyncio.sleep(1)  # shutdown handshake never answers in time

    app = Starlette(routes=[Route("/boot", _startup_only)], lifespan=_lifespan)
    with pytest.raises(RuntimeError) as excinfo:
        await asyncio.wait_for(capture_all(app, ["/boot"]), timeout=5)
    assert "lifespan shutdown timed out after 0.05s" in str(excinfo.value)
