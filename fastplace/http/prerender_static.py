"""Serve prerendered pages ahead of the ASGI app for browser navigations.

``fastplace prerender`` writes static HTML under ``public/build/prerender``;
this middleware serves those files for plain GET navigations — method GET,
``Accept: text/html``, no query string — and falls through to the live app
for everything else (forms, API clients, cache-busting queries). The lookup
runs before the router because routes are mounted ahead of the static
mounts in the kernel: a ``StaticFiles`` subclass at the public mount could
never win a path the router already claims.
"""

from __future__ import annotations

from pathlib import Path

from starlette.responses import FileResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_INDEX = "index.html"

# The capture engine's bypass: a request carrying this header wants the
# live app (a `fastplace prerender` re-run refreshing this very directory),
# not the stale file this middleware would otherwise serve.
_CAPTURE_MARKER = b"x-fastplace-prerender-capture"


def _accepts_html(scope: Scope) -> bool:
    headers = scope.get("headers") or []
    if any(key == _CAPTURE_MARKER for key, _value in headers):
        return False
    return any(key == b"accept" and b"text/html" in value.lower() for key, value in headers)


def _prerender_file(route_path: str, prerender_dir: Path) -> Path | None:
    """Map a request path onto ``<prerender_dir>/<segments>/index.html``.

    Returns ``None`` for anything that is not a clean absolute path —
    traversal segments and empty segments fall through to the app, which
    answers them exactly as it would without prerendering.
    """
    cleaned = route_path.rstrip("/")
    segments = cleaned.split("/")[1:] if cleaned else []
    if any(segment in ("", "..") for segment in segments):
        return None
    directory = prerender_dir.joinpath(*segments) if segments else prerender_dir
    candidate = directory / _INDEX
    # Serve-side parity with the writer's containment check: a symlink
    # planted inside the tree must not serve its target's file. Both sides
    # resolved — macOS maps /tmp onto /private/tmp and an unresolved
    # comparison would misfire there.
    if not candidate.resolve().is_relative_to(prerender_dir.resolve()):
        return None
    return candidate


class PrerenderStaticFiles:
    """Pure-ASGI prerender lookup — smallest change that outranks the router.

    The spec names this class at the public mount; it lives as middleware
    instead (same name, same serving contract) because Starlette matches
    routes before mounts, and prerendered pages share paths with bridge
    routes. The existing ``/build`` and ``/`` mounts are untouched.
    """

    def __init__(self, app: ASGIApp, prerender_dir: Path | str) -> None:
        self.app = app
        self.prerender_dir = Path(prerender_dir)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            eligible = (
                scope.get("method") == "GET"
                and not scope.get("query_string")
                and _accepts_html(scope)
            )
            if eligible:
                candidate = _prerender_file(scope.get("path", ""), self.prerender_dir)
                # Per-request existence check: a project that never ran
                # `fastplace prerender` boots and serves exactly as before.
                if candidate is not None and candidate.is_file():
                    # Un-hashed content like the public root: revalidate
                    # quickly rather than cache immutably.
                    response = FileResponse(
                        candidate,
                        media_type="text/html",
                        headers={"Cache-Control": "public, max-age=300"},
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
