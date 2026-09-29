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
