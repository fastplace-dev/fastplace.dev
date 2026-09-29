"""In-process ASGI capture — fetch routes through the real app stack.

The engine drives the application callable directly (httpx ASGI transport,
no network): middleware, routing, and the ASGI lifespan all run exactly as
they do under ``fastplace serve``, so a captured page is byte-identical to
the served one.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

# A host that can never collide with a real one; ASGI transports never
# resolve it, but httpx needs an absolute base URL.
_BASE_URL = "http://prerender.internal"

# Browsers navigate with an HTML accept header; the captured request should
# look like the traffic the prerendered files will later serve.
_HEADERS = {"accept": "text/html"}


@dataclass(frozen=True)
class CapturedPage:
    """One route's capture result — facts only, callers judge them."""

    route: str
    status: int
    content_type: str
    body: bytes


async def capture_route(app: Any, route: str) -> CapturedPage:
    """GET ``route`` through ``app`` and return what came back.

    Redirects are not followed: a redirecting route is a fact for the caller
    to report, not something to chase into a different page.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=_BASE_URL,
    ) as client:
        return await _fetch(client, route)


async def capture_all(app: Any, routes: list[str]) -> list[CapturedPage]:
    """Capture every route sequentially, under one application lifespan.

    Sequential on purpose: app boot hooks (DB engines, caches) are not
    re-entrant per capture, and deterministic order makes output diffs and
    failure reports readable. The lifespan runs around the whole batch —
    startup before the first fetch, shutdown after the last — so pages see
    the same initialized state the live server gives them.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=_BASE_URL,
    ) as client:
        async with _lifespan(app):
            return [await _fetch(client, route) for route in routes]


async def _fetch(client: httpx.AsyncClient, route: str) -> CapturedPage:
    response = await client.get(route, headers=_HEADERS, follow_redirects=False)
    return CapturedPage(
        route=route,
        status=response.status_code,
        content_type=response.headers.get("content-type", ""),
        body=response.content,
    )


@contextlib.asynccontextmanager
async def _lifespan(app: Any) -> AsyncIterator[None]:
    """Run the app's ASGI lifespan without the asgi-lifespan package.

    The dev-only ``asgi_lifespan`` helper cannot be a runtime dependency of
    the CLI, but skipping the lifespan silently would drop startup hooks
    (``lifecycle.run_startup``) and drift captures from served pages — so
    the protocol is spoken here directly: send ``lifespan.startup``, require
    ``startup.complete``, and mirror the shutdown handshake. A broken app
    that never answers (or dies mid-handshake) fails via timeout instead of
    hanging the CLI forever.
    """
    import asyncio

    handshake_timeout = 30.0

    scope: dict[str, Any] = {
        "type": "lifespan",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
    }
    to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    from_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    task = asyncio.create_task(app(scope, to_app.get, from_app.put))

    async def _receive() -> dict[str, Any]:
        """Next lifespan message, or the app coroutine's own exception."""
        receive_task = asyncio.create_task(from_app.get())
        done, _ = await asyncio.wait({receive_task, task}, return_when=asyncio.FIRST_COMPLETED)
        if receive_task in done:
            return receive_task.result()
        # The app died without a protocol message — surface its error.
        receive_task.cancel()
        task.result()
        raise RuntimeError("app exited during lifespan handshake")

    try:
        await to_app.put({"type": "lifespan.startup"})
        message = await asyncio.wait_for(_receive(), timeout=handshake_timeout)
        if message["type"] == "lifespan.startup.failed":
            raise RuntimeError(f"app lifespan startup failed: {message.get('message', '')}")
        yield
    finally:
        # Shutdown still runs when the body raised: the captures are done,
        # but engines and listeners the startup opened deserve a close.
        await to_app.put({"type": "lifespan.shutdown"})
        message = await asyncio.wait_for(_receive(), timeout=handshake_timeout)
        if message["type"] == "lifespan.shutdown.failed":
            raise RuntimeError(f"app lifespan shutdown failed: {message.get('message', '')}")
        await task
