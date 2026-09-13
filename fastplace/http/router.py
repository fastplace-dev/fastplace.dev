"""Fastplace Router — route declaration surface for routes/{web,api,ai}.py.

Route files declare controllers against a ``Router``; the kernel mounts the
web router at the root, the API router under ``/api/v1``, and the AI router
under ``/ai``. Controllers are thin: they receive a :class:`Request` and
return anything coercible to a response.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from starlette.requests import Request as StarletteRequest


@dataclass(frozen=True)
class Route:
    """One registered HTTP route."""

    method: str
    path: str
    handler: Callable
    name: str | None = None


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

    def add(self, method: str, path: str, handler: Callable, name: str | None = None) -> Route:
        route = Route(method=method.upper(), path=self._join(path), handler=handler, name=name)
        self.routes.append(route)
        return route

    def get(self, path: str, handler: Callable, name: str | None = None) -> Route:
        return self.add("GET", path, handler, name)

    def post(self, path: str, handler: Callable, name: str | None = None) -> Route:
        return self.add("POST", path, handler, name)

    def put(self, path: str, handler: Callable, name: str | None = None) -> Route:
        return self.add("PUT", path, handler, name)

    def patch(self, path: str, handler: Callable, name: str | None = None) -> Route:
        return self.add("PATCH", path, handler, name)

    def delete(self, path: str, handler: Callable, name: str | None = None) -> Route:
        return self.add("DELETE", path, handler, name)

    def websocket(self, path: str, handler: Callable, name: str | None = None) -> WebSocketRoute:
        route = WebSocketRoute(path=self._join(path), handler=handler, name=name)
        self.websocket_routes.append(route)
        return route

    def _join(self, path: str) -> str:
        if not self.prefix:
            return path
        return self.prefix.rstrip("/") + "/" + path.lstrip("/")

    def extend(self, other: "Router") -> None:
        """Append every route from another router."""
        self.routes.extend(other.routes)
        self.websocket_routes.extend(other.websocket_routes)


class Controller:
    """Optional base class for controllers.

    Plain ``async def handler(request)`` functions are equally valid; grouped
    controllers may subclass this for shared helpers. Subclasses must never
    contain business logic (CSR layering).
    """


def endpoint_adapter(handler: Callable) -> Callable:
    """Adapt a Fastplace controller into a FastAPI-compatible endpoint.

    FastAPI injects its request; we wrap it, call the controller, and coerce
    the return value. If the controller annotates a Pydantic model as its
    return type, the outbound payload is validated against it first — the
    unified API's automatic response serialization.
    """
    from fastplace.http.request import Request
    from fastplace.http.response import to_response

    from .serialization import validated_payload

    async def endpoint(starlette_request: StarletteRequest) -> Any:
        request = Request(starlette_request)
        result = await handler(request)
        return to_response(validated_payload(handler, result))

    endpoint.__name__ = getattr(handler, "__name__", "endpoint")
    return endpoint
