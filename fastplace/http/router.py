"""Fastplace Router — route declaration surface for routes/{web,api,ai}.py.

Route files declare controllers against a ``Router``; the kernel mounts the
web router at the root, the API router under ``/api/v1``, and the AI router
under ``/ai``. Controllers are thin: they receive a :class:`Request` and
return anything coercible to a response.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import Request as StarletteRequest


@dataclass(frozen=True)
class Route:
    """One registered HTTP route."""

    method: str
    path: str
    handler: Callable
    name: str | None = None
    middleware: tuple[str, ...] = ()


@dataclass(frozen=True)
class WebSocketRoute:
    """One registered WebSocket route."""

    path: str
    handler: Callable
    name: str | None = None


@dataclass
class Router:
    """Collects routes for one surface (web, api, ai)."""

    prefix: str = ""
    routes: list[Route] = field(default_factory=list)
    websocket_routes: list[WebSocketRoute] = field(default_factory=list)
    _group_middleware: tuple[str, ...] = field(default=(), init=False, repr=False)

    def add(
        self,
        method: str,
        path: str,
        handler: Callable,
        action: str | None = None,
        name: str | None = None,
        middleware: list[str] | tuple[str, ...] | None = None,
    ) -> Route:
        # declarative registration: router.get(path, Controller, "action").
        # Resolve the bound method once at registration time so the kernel's
        # endpoint adapter receives a plain callable either way.
        if action is not None:
            if not inspect.isclass(handler):
                raise TypeError(
                    "router action= requires a Controller class — pass a plain "
                    f"handler instead, got {handler!r}"
                )
            bound = getattr(handler(), action, None)
            if bound is None:
                raise AttributeError(f"{handler.__name__} has no action '{action}'")
            handler = bound
        route = Route(
            method=method.upper(),
            path=self._join(path),
            handler=handler,
            name=name,
            middleware=self._group_middleware + tuple(middleware or ()),
        )
        self.routes.append(route)
        return route

    def get(
        self,
        path: str,
        handler: Callable,
        action: str | None = None,
        name: str | None = None,
        middleware: list[str] | tuple[str, ...] | None = None,
    ) -> Route:
        return self.add("GET", path, handler, action, name, middleware)

    def post(
        self,
        path: str,
        handler: Callable,
        action: str | None = None,
        name: str | None = None,
        middleware: list[str] | tuple[str, ...] | None = None,
    ) -> Route:
        return self.add("POST", path, handler, action, name, middleware)

    def put(
        self,
        path: str,
        handler: Callable,
        action: str | None = None,
        name: str | None = None,
        middleware: list[str] | tuple[str, ...] | None = None,
    ) -> Route:
        return self.add("PUT", path, handler, action, name, middleware)

    def patch(
        self,
        path: str,
        handler: Callable,
        action: str | None = None,
        name: str | None = None,
        middleware: list[str] | tuple[str, ...] | None = None,
    ) -> Route:
        return self.add("PATCH", path, handler, action, name, middleware)

    def delete(
        self,
        path: str,
        handler: Callable,
        action: str | None = None,
        name: str | None = None,
        middleware: list[str] | tuple[str, ...] | None = None,
    ) -> Route:
        return self.add("DELETE", path, handler, action, name, middleware)

    def websocket(self, path: str, handler: Callable, name: str | None = None) -> WebSocketRoute:
        route = WebSocketRoute(path=self._join(path), handler=handler, name=name)
        self.websocket_routes.append(route)
        return route

    @contextmanager
    def group(self, prefix: str = "", middleware: list[str] | None = None) -> Iterator[Router]:
        """Declare routes sharing a prefix and/or a middleware stack."""
        previous_prefix = self.prefix
        previous_middleware = self._group_middleware
        if prefix:
            self.prefix = self._join(prefix)
        self._group_middleware = previous_middleware + tuple(middleware or ())
        try:
            yield self
        finally:
            self.prefix = previous_prefix
            self._group_middleware = previous_middleware

    def _join(self, path: str) -> str:
        if not self.prefix:
            return path
        return self.prefix.rstrip("/") + "/" + path.lstrip("/")

    def extend(self, other: Router) -> None:
        """Append every route from another router."""
        self.routes.extend(other.routes)
        self.websocket_routes.extend(other.websocket_routes)


class Controller:
    """Optional base class for controllers.

    Plain ``async def handler(request)`` functions are equally valid; grouped
    controllers may subclass this for shared helpers. Subclasses must never
    contain business logic (CSR layering).
    """


def resolve_route_middleware(registry: dict[str, Any], spec: str) -> Any:
    """Resolve one ``alias[:arg1,arg2]`` route-middleware entry (spec §4.5).

    Registry values are ``Middleware`` instances (arg-less aliases) or
    callables — a class or factory — invoked with the parsed args
    (parameterized aliases like ``throttle:5,60``).
    """
    from fastplace.errors import ConfigurationError
    from fastplace.http.middleware import Middleware as HttpMiddleware

    name, _, arg_str = spec.partition(":")
    args = [part.strip() for part in arg_str.split(",") if part.strip()] if arg_str else []
    entry = registry.get(name)
    if entry is None:
        raise ConfigurationError(
            f"unknown route middleware alias {name!r} — register it in "
            "ROUTE_MIDDLEWARE (config/app.py) or get_app(route_middleware=...)"
        )
    if isinstance(entry, HttpMiddleware):
        if args:
            raise ConfigurationError(
                f"route middleware {name!r} is a prebuilt instance and takes no arguments"
            )
        return entry
    built = entry(*args)
    if not isinstance(built, HttpMiddleware):
        raise ConfigurationError(
            f"route middleware {name!r} did not produce a fastplace.http.Middleware"
        )
    return built


def _bind(mw: Any, next_step: Any) -> Any:
    # Module-level binding: a closure built inside a loop would capture the
    # loop variable late — every step would call the last middleware.
    async def step(request: Any) -> Any:
        return await mw.handle(request, next_step)

    return step


def endpoint_adapter(handler: Callable, middleware: tuple[Any, ...] = ()) -> Callable:
    """Adapt a Fastplace controller into a FastAPI-compatible endpoint.

    FastAPI injects its request; we wrap it, optionally run the route's
    middleware chain (outermost first) around the controller call, and
    coerce the return value (Pydantic return annotations validate first).
    """
    from fastplace.http.request import Request
    from fastplace.http.response import to_response

    from .serialization import validated_payload

    async def run_controller(request: Request) -> Any:
        result = await handler(request)
        return to_response(validated_payload(handler, result))

    async def endpoint(starlette_request: StarletteRequest) -> Any:
        request = Request(starlette_request)
        if not middleware:
            result = await handler(request)
            return to_response(validated_payload(handler, result))
        core: Any = run_controller
        for mw in reversed(middleware):
            core = _bind(mw, core)
        return await core(request)

    endpoint.__name__ = getattr(handler, "__name__", "endpoint")
    return endpoint
