"""Auth HTTP middleware — request.user resolution + CSRF protection.

Registered through ``config/app.py`` (``MIDDLEWARE``); both pieces honor the
two edges from the blueprint: the web edge (session + CSRF) and the API edge
(stateless Bearer tokens, CSRF not applicable).

Order matters and is declared in ``config/app.py`` (first declared =
outermost): ResolveUserMiddleware runs before CsrfMiddleware so the resolved
auth edge (``scope["fastplace_auth_edge"]``) tells CSRF whether the request
is genuinely token-authenticated.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Any

from fastplace.auth.guards import guard
from fastplace.errors import AuthenticationError, FastplaceError
from fastplace.http.middleware import Middleware
from fastplace.http.request import Request
from fastplace.http.response import Json, Response

CSRF_HEADER = "X-Fastplace-CSRF-Token"
CSRF_SESSION_KEY = "_token"
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
AUTH_EDGE_KEY = "fastplace_auth_edge"


class ResolveUserMiddleware(Middleware):
    """Resolve ``request.user`` before the controller runs.

    A Bearer token (stateless edge) takes precedence when present; otherwise
    the default guard — session by default — resolves the identity. A Bearer
    token that was *presented but invalid* is rejected with 401 instead of
    silently degrading to the session identity (confused-principal defense);
    an unauthenticated request simply carries ``user = None``.
    """

    async def handle(self, request: Request, call_next) -> Response:
        try:
            user = await self._resolve(request)
        except AuthenticationError as exc:
            return Json({"error": str(exc) or "Invalid credentials."}, status_code=401)
        request.set_user(user)
        return await call_next(request)

    async def _resolve(self, request: Request) -> Any:
        token_guard = self._token_guard_or_none()
        if token_guard is not None and token_guard.extract(request):
            user = await token_guard.user(request)
            if user is not None:
                request.scope[AUTH_EDGE_KEY] = "token"
                return user
            raise AuthenticationError("Invalid or expired token.")
        user = await guard().user(request)
        request.scope[AUTH_EDGE_KEY] = "session" if user is not None else None
        return user

    def _token_guard_or_none(self):
        try:
            return guard("token")
        except FastplaceError:
            return None  # no APP_KEY configured → token edge simply unavailable


class CsrfMiddleware(Middleware):
    """Issue a session-bound token and validate every unsafe method.

    Tokens travel via the ``X-Fastplace-CSRF-Token`` header (the React bridge
    reads the ``<meta name="csrf-token">`` tag render() embeds and echoes it
    back). Requests genuinely authenticated by the token guard and
    ``CSRF_EXCEPT`` paths are exempt. Mere presence of an ``Authorization``
    header is NOT an exemption — a session plus any Authorization value fails
    closed to the token check (OWASP CSRF).
    """

    async def handle(self, request: Request, call_next) -> Response:
        if request.method in CSRF_SAFE_METHODS or self._is_exempt(request):
            # Issue before the stack runs so render() can embed the token in
            # the very first HTML response (the bridge reads the meta tag).
            token = self._ensure_token(request)
            response = await call_next(request)
            response.headers[CSRF_HEADER] = token
            return response

        if request.scope.get(AUTH_EDGE_KEY) == "token":
            # Token-authenticated requests carry no ambient session
            # credentials — CSRF is a session-edge concern (OWASP CSRF).
            return await call_next(request)

        expected = request.session.get(CSRF_SESSION_KEY)
        supplied = await self._supplied_token(request)
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            return Json({"error": "CSRF token mismatch."}, status_code=403)

        response = await call_next(request)
        # Re-read: login() rotates the token across the privilege boundary,
        # and the response must advertise the fresh one.
        response.headers[CSRF_HEADER] = request.session.get(CSRF_SESSION_KEY) or expected
        return response

    # -- helpers -------------------------------------------------------------

    def _ensure_token(self, request: Request) -> str:
        token = request.session.get(CSRF_SESSION_KEY)
        if not token:
            token = secrets.token_urlsafe(32)
            request.session[CSRF_SESSION_KEY] = token
        return token

    async def _supplied_token(self, request: Request) -> str | None:
        header = request.header(CSRF_HEADER)
        if header:
            return header
        content_type = (request.header("Content-Type") or "").lower()
        if "application/json" in content_type:
            try:
                await request.body()  # cache the stream for downstream re-reads
                payload = await request.json()
            except Exception:
                return None
            if isinstance(payload, dict):
                value = payload.get("_token")
                return value if isinstance(value, str) else None
        if "form-urlencoded" in content_type or "multipart/form-data" in content_type:
            try:
                # form() consumes the receive stream without populating the
                # body cache — read the body first so BaseHTTPMiddleware can
                # replay it for the controller downstream.
                await request.body()
                form = await request.form()
            except Exception:
                return None  # unparseable body fails closed
            value = form.get("_token")
            return value if isinstance(value, str) else None
        return None

    def _is_exempt(self, request: Request) -> bool:
        from fastplace.config import config

        raw = config("CSRF_EXCEPT", []) or []
        patterns = raw.split(",") if isinstance(raw, str) else list(raw)
        path = request.path
        for pattern in patterns:
            pattern = str(pattern).strip()
            if not pattern:
                continue
            if pattern.endswith("/*"):
                if path.startswith(pattern[:-1]):
                    return True
            elif path == pattern:
                return True
        return False
