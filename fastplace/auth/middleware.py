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
import time
from typing import Any

from fastplace.auth.guards import TWO_FACTOR_CHALLENGE_KEY, guard
from fastplace.auth.remember import (
    REMEMBER_COOKIE_NAME,
    REMEMBER_COOKIE_SCOPE,
    REMEMBER_COOKIE_TTL,
)
from fastplace.authz.gate import gate
from fastplace.config import config
from fastplace.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    FastplaceError,
)
from fastplace.http.middleware import Middleware
from fastplace.http.request import Request
from fastplace.http.response import Json, Redirect, Response

CSRF_HEADER = "X-Fastplace-CSRF-Token"
CSRF_SESSION_KEY = "_token"
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
AUTH_EDGE_KEY = "fastplace_auth_edge"

#: Flashed on redirect-back when a browser post carries a stale token —
#: keyed under ``_token`` so the form re-renders with a fresh one.
CSRF_EXPIRED_MESSAGE = "The page has expired. Please try again."

#: Sentinel distinguishing "no queued cookie" from a queued clear (None).
_UNSET = object()

INTENDED_SESSION_KEY = "url.intended"

#: Scope key carrying the precomputed `{ability: bool}` map (spec §4.16).
AUTH_CAN_SCOPE = "fastplace_auth_can"


def _shared_abilities() -> list[str]:
    """AUTH_SHARED_ABILITIES as a clean list — env strings arrive unsplit."""
    raw = config("AUTH_SHARED_ABILITIES", default=[])
    if isinstance(raw, str):
        return [ability.strip() for ability in raw.split(",") if ability.strip()]
    return list(raw or [])


class SharedAbilitiesMiddleware(Middleware):
    """Precompute the shared can-map before the handler runs (spec §4.16).

    ``page_payload`` and ``render`` are sync, so share callbacks cannot
    await gate checks — the async work lives here instead, one layer out,
    stashing ``{ability: bool}`` in the request scope for the sync
    ``shared_auth_props`` callback to read. Declared after
    ``ResolveUserMiddleware`` so ``request.user`` is already resolved; a
    ``before()`` hook may deliberately allow guests, so guests flow through
    the gate like anyone else. With the default empty list this is a no-op
    stash of ``{}``.
    """

    async def handle(self, request: Request, call_next: Any) -> Any:
        abilities = _shared_abilities()
        if abilities:
            bound = gate.for_user(request.user)
            request.scope[AUTH_CAN_SCOPE] = {
                ability: await bound.allows(ability) for ability in abilities
            }
        else:
            request.scope[AUTH_CAN_SCOPE] = {}
        return await call_next(request)


def shared_auth_props(request: Request) -> dict[str, Any] | None:
    """Sync share callback — the auth snapshot every page payload carries.

    The can-map was precomputed by ``SharedAbilitiesMiddleware``; a request
    that never crossed it simply renders an empty map. Lookups stay
    defensive because a registered share runs for EVERY render() call
    site, and edge/test stubs may not carry the full Request surface.
    """
    user = getattr(request, "user", None)
    scope = getattr(request, "scope", None) or {}
    serialized = user.to_dict() if user is not None and hasattr(user, "to_dict") else user
    return {"auth": {"user": serialized, "can": scope.get(AUTH_CAN_SCOPE, {})}}


def _remember_cookie_header(value: str | None, request: Request) -> str:
    """Set-Cookie line for the remember cookie — ``value=None`` clears it."""
    if value is None:
        return f"{REMEMBER_COOKIE_NAME}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"
    cookie = (
        f"{REMEMBER_COOKIE_NAME}={value}; Max-Age={REMEMBER_COOKIE_TTL}; "
        "Path=/; HttpOnly; SameSite=Lax"
    )
    if str(request.url).startswith("https"):
        cookie += "; Secure"
    return cookie


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
            return Json({"message": str(exc) or "Invalid credentials."}, status_code=401)
        request.set_user(user)
        response = await call_next(request)
        pending = request.scope.get(REMEMBER_COOKIE_SCOPE, _UNSET)
        if pending is not _UNSET:
            # A guard queued a remember-cookie write (or clear) during the
            # request; guards never touch responses, so flush it here on
            # the way out (spec §4.6).
            response.headers.append("set-cookie", _remember_cookie_header(pending, request))
        return response

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
            # A browser form post with a stale/expired token gets the same
            # no-JS redirect-back treatment as a validation failure — the
            # visitor keeps their typed input and sees the expiry message.
            # Bridge and API clients keep the 419 JSON envelope.
            from fastplace.http.redirect_back import redirect_back_with_errors, wants_redirect_back

            if wants_redirect_back(request):
                return redirect_back_with_errors(
                    request, {CSRF_SESSION_KEY: [CSRF_EXPIRED_MESSAGE]}
                )
            return Json({"message": "CSRF token mismatch."}, status_code=419)

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


class AuthenticateMiddleware(Middleware):
    """``auth`` route middleware — reject anonymous requests (spec §4.5).

    Route middleware runs inside the endpoint (after ResolveUserMiddleware),
    so ``request.user`` is already resolved here. Programmatic surfaces —
    bridge payloads, /api/v1, and the /ai streams — get the 401 JSON
    envelope (the SPA's fetch() follows a redirect silently and would
    SSE-parse the login page as event data); browser navigations are
    redirected to /login with the intended URL parked in the session for
    ``request.intended()`` to resume.
    """

    async def handle(self, request: Request, call_next) -> Response:
        if request.user is not None:
            return await call_next(request)
        if request.is_bridge or request.path.startswith(("/api/", "/ai/")):
            raise AuthenticationError()
        # Credentials were already proven but the challenge is outstanding
        # (password login or the remember fallback parked it) — steer to the
        # challenge page, not /login, which would ask for a password again.
        if request.session.get(TWO_FACTOR_CHALLENGE_KEY) is not None:
            return Redirect("/two-factor-challenge", status_code=302)
        request.session[INTENDED_SESSION_KEY] = request.full_path
        return Redirect("/login", status_code=302)


class GuestMiddleware(Middleware):
    """``guest`` route middleware — authenticated users bounce to the app.

    An authenticated visitor is redirected to the URL parked by ``auth``
    middleware (the journey they were on before login) or the dashboard.
    """

    async def handle(self, request: Request, call_next) -> Response:
        if request.user is None:
            return await call_next(request)
        intended = request.session.pop(INTENDED_SESSION_KEY, None) or "/dashboard"
        return Redirect(intended, status_code=302)


class EnsureEmailVerifiedMiddleware(Middleware):
    """``verified`` route middleware — unverified users bounce to the notice page."""

    async def handle(self, request: Request, call_next) -> Response:
        user = request.user
        if user is None or getattr(user, "email_verified_at", None) is not None:
            return await call_next(request)
        if request.path.startswith("/api/"):
            raise AuthorizationError("Your email address is not verified.")
        # Browser AND bridge: 302 redirect (GuestMiddleware precedent) — a 403
        # envelope would strand the SPA after register → /dashboard.
        return Redirect("/email/verify", status_code=302)


class EnsurePasswordConfirmedMiddleware(Middleware):
    """``password.confirm`` route middleware — re-verify password on sensitive
    pages when the last confirmation is older than ``PASSWORD_TIMEOUT``
    (spec §4.5/§4.12). Browsers and bridge GETs get a 302 onto the confirm
    page (the SPA swaps); API-shaped requests get the 403 envelope.
    """

    def _confirmed(self, request: Request) -> bool:
        from fastplace.config import config

        raw = request.session.get("password_confirmed_at")
        if not isinstance(raw, (int, float)):
            return False
        timeout = float(config("PASSWORD_TIMEOUT", default=10800) or 10800)
        # The stored stamp is an int UNIX-second value (Task 5 writes it), so
        # the elapsed comparison runs at whole-second granularity — a stamp
        # recorded exactly timeout seconds ago still counts as confirmed.
        return (int(time.time()) - int(raw)) <= timeout

    def _wants_json_envelope(self, request: Request) -> bool:
        if request.path.startswith("/api/"):
            return True
        accept = request.header("Accept") or ""
        return "application/json" in accept and not request.is_bridge

    async def handle(self, request: Request, call_next) -> Response:
        if self._confirmed(request):
            return await call_next(request)
        if self._wants_json_envelope(request):
            raise AuthorizationError("Password confirmation required.")
        request.session[INTENDED_SESSION_KEY] = request.full_path
        return Redirect("/user/confirm-password", status_code=302)


def _anonymous_login_redirect(request: Request) -> Response:
    """The auth-middleware anonymous contract, shared by the ability gates."""
    if request.is_bridge or request.path.startswith("/api/"):
        raise AuthenticationError()
    request.session[INTENDED_SESSION_KEY] = request.full_path
    return Redirect("/login", status_code=302)


class AbilitiesMiddleware(Middleware):
    """``abilities:a,b`` — a PAT bearer must hold EVERY listed ability (§4.5).

    Session-authenticated requests pass unconditionally (spec §4.14:
    ``token_can`` answers True off the PAT edge); anonymous requests get the
    ``auth`` middleware treatment — 401 envelope on the API edge, a login
    redirect with the intended URL parked for browsers.
    """

    _MODE_ALL = True

    def __init__(self, *args: str) -> None:
        self.abilities = [arg.strip() for arg in args if arg.strip()]
        if not self.abilities:
            raise ConfigurationError(
                "abilities middleware expects at least one ability (abilities:orders,posts)"
            )

    async def handle(self, request: Request, call_next) -> Response:
        if request.user is None:
            return _anonymous_login_redirect(request)
        if self._mode_all():
            ok = all(request.token_can(ability) for ability in self.abilities)
        else:
            ok = any(request.token_can(ability) for ability in self.abilities)
        if not ok:
            raise AuthorizationError("Invalid token ability.")
        return await call_next(request)

    def _mode_all(self) -> bool:
        return self._MODE_ALL


class AbilityMiddleware(AbilitiesMiddleware):
    """``ability:a,b`` — a PAT bearer needs ANY ONE of the listed abilities."""

    _MODE_ALL = False
