"""Fastplace middleware — conventions above Starlette's middleware stack."""

from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from fastplace.http.request import Request
from fastplace.http.response import Response


class Middleware:
    """Base class for Fastplace HTTP middleware.

    Subclasses implement :meth:`handle` — run logic before/after the rest of
    the stack by awaiting ``call_next(request)`` and optionally mutating the
    returned response. Registration happens through ``config/app.py``
    (``MIDDLEWARE``) or programmatically via ``get_app(middleware=[...])``.
    """

    async def handle(self, request: Request, call_next) -> Response:
        return await call_next(request)


def wrap_middleware(middleware: Middleware) -> type[BaseHTTPMiddleware]:
    """Build a Starlette ``BaseHTTPMiddleware`` subclass around an instance."""

    class WrappedFastplaceMiddleware(BaseHTTPMiddleware):
        def __init__(self, app: ASGIApp, mw: Middleware = middleware) -> None:
            super().__init__(app)
            self.mw = mw

        async def dispatch(self, request: Any, call_next: Any) -> Response:
            fastplace_request = Request(request)

            async def next_(req: Request) -> Response:
                return await call_next(req.starlette)

            return await self.mw.handle(fastplace_request, next_)

    WrappedFastplaceMiddleware.__name__ = type(middleware).__name__
    return WrappedFastplaceMiddleware
