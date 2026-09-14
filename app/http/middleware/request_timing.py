"""Request timing middleware — surfaces handler time on every response."""

from __future__ import annotations

import time

from fastplace.http.middleware import Middleware
from fastplace.http.request import Request
from fastplace.http.response import Response

#: Header carrying the wall-clock seconds the request spent in the app.
HEADER = "x-process-time"


class RequestTimingMiddleware(Middleware):
    """Stamp ``X-Process-Time`` on every response (seconds, 3 decimals).

    App-level middleware living where the blueprint wants it
    (``app/http/middleware/``), registered from ``config/app.py`` — the
    framework no longer holds the whole stack alone.
    """

    async def handle(self, request: Request, call_next) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started
        response.headers[HEADER] = f"{elapsed:.3f}"
        return response
