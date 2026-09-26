"""Shared no-JS redirect-back helpers — validation failures and CSRF expiry.

Browser form posts (unsafe method, ``text/html`` accept, a live session)
get a 303 back to the form with errors flashed on the session; the SPA
bridge and API clients keep their JSON envelopes. These helpers live
outside the kernel so auth middleware can reuse them without an import
cycle.
"""

from __future__ import annotations

from typing import Any

from fastplace.http.response import Response


def wants_redirect_back(request: Any) -> bool:
    """True when a failure should redirect back, not answer the JSON envelope.

    Browser form posts (unsafe method, ``text/html`` accept, no bridge
    header) re-render their form; the SPA bridge and API clients keep the
    JSON envelope. A session is required to flash the errors — without one
    the errors would be lost on the redirect.
    """
    from fastplace.http.error_pages import wants_html

    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return False
    if not wants_html(request):
        return False
    try:
        return isinstance(request.session, dict)
    except Exception:
        return False


def safe_back_url(request: Any) -> str:
    """The referer when it stays on this origin, else a GET-servable local path.

    The own-path fallback only keeps the request's path when a registered
    route actually answers GET for it — the failure may have happened on a
    POST-only endpoint hit directly, and a 303 onto it would land on a 404
    or 405 (or loop). Anything else falls back to ``/``.
    """
    from urllib.parse import urlsplit

    referer = request.headers.get("referer")
    if referer:
        netloc = urlsplit(referer).netloc
        # A relative referer has no authority; a cross-site one must never
        # become the redirect target (open-redirect guard).
        if not netloc or netloc == request.headers.get("host", ""):
            return referer
    scope = getattr(request, "scope", None) or {}
    path = scope.get("path", "/") or "/"
    query_string = scope.get("query_string")
    candidate = path + (f"?{query_string.decode('latin-1')}" if query_string else "")
    if _path_serves_get(scope, path):
        return candidate
    return "/"


def redirect_back_with_errors(request: Any, errors: dict[str, list[str]]) -> Response:
    """Flash ``errors`` on the session and 303 back to the form."""
    from fastplace.http.flash import flash_errors
    from fastplace.http.response import Redirect

    flash_errors(request, errors or {})
    return Redirect(safe_back_url(request), status_code=303)


def _path_serves_get(scope: dict[str, Any], path: str) -> bool:
    """True when some registered route answers GET for ``path``.

    Reads the ASGI app's route table off the scope (Starlette mounts it as
    ``scope["app"]``) and lets the framework's own matcher resolve paths,
    prefixes, and parameters. A bare scope — unit-test stubs, missing app —
    must fail closed to the caller's safe default, never raise.
    """
    routes = getattr(scope.get("app"), "routes", None)
    if not routes:
        return False
    try:
        from starlette.routing import Match, Mount

        probe = {"type": "http", "method": "GET", "path": path}
        for route in routes:
            if isinstance(route, Mount):
                # A catch-all static mount answers every method but only
                # serves files — it would 404 the follow-up GET, so it
                # never counts as GET-servable here.
                continue
            match, _ = route.matches(probe)
            if match is Match.FULL:
                return True
    except Exception:
        return False
    return False
