"""Auth guards — session (server-side store) and token (JWT) strategies.

Both guards answer the same two questions: *log this user in / out* and
*who is the user on this request*. Guards resolve users through a
:class:`~fastplace.auth.providers.UserProvider`; they never query storage
themselves. Selection is configuration-driven (``config/auth.py``):

    AUTH_DEFAULT_GUARD = "session"
    AUTH_GUARDS = {"session": {"driver": "session"}, "token": {"driver": "jwt", ...}}
"""

from __future__ import annotations

import hashlib
import inspect
import secrets
import warnings
from typing import Any

import jwt

from fastplace.auth.providers import UserProvider, provider_from_config
from fastplace.auth.remember import (
    REMEMBER_COOKIE_NAME,
    REMEMBER_COOKIE_SCOPE,
    VIA_REMEMBER_SCOPE,
    remember_store,
)
from fastplace.errors import ConfigurationError, ThrottleRequestsError
from fastplace.events import DomainEvent, dispatch
from fastplace.ratelimit import RateLimiter

BEARER_SCHEME = "Bearer"

#: Scope key ServerSessionMiddleware uses to expose the live session store
#: (set in ``fastplace/http/session/middleware.py``) — ``logout_other_devices``
#: reads it for per-user revocation.
SESSION_STORE_SCOPE = "fastplace_session_store"


class SessionGuard:
    """Stateful guard backed by the server-side session (spec §4.2/§4.6).

    The kernel installs ``ServerSessionMiddleware`` (opaque-ID cookie over
    a pluggable store: memory/database/redis), so ``request.session`` is a
    server-backed dict; the guard only writes the user identifier into it.
    The credential core adds the rate-limited ``attempt`` family, the
    rotating remember-me cookie fallback, and the Login/Logout lifecycle
    events (payloads are JSON-safe — never an ORM instance).
    """

    SESSION_KEY = "user_id"

    def __init__(
        self,
        provider: UserProvider,
        *,
        limiter: RateLimiter | None = None,
        max_attempts: int = 5,
        decay: int = 60,
    ) -> None:
        self.provider = provider
        self._limiter = limiter
        self._max_attempts = max_attempts
        self._decay = decay

    def _rate_limiter(self) -> RateLimiter:
        # Lazy: unit-test construction stays cheap and config-free.
        if self._limiter is None:
            self._limiter = RateLimiter()
        return self._limiter

    def _login_key(self, request: Any, email: str) -> str:
        # One email's lockout never locks out another (Review Focus #3) —
        # the key is sha1(lowercased email | client ip).
        ip = getattr(request, "ip", None) or ""
        return hashlib.sha1(f"{email.lower()}|{ip}".encode()).hexdigest()

    async def attempt(
        self, request: Any, credentials: dict[str, Any], *, remember: bool = False
    ) -> bool:
        """Rate-limited credential login. Raises ThrottleRequestsError on lockout."""
        email = str(credentials.get("email") or "")
        await dispatch(DomainEvent("Attempting", {"email": email.lower(), "remember": remember}))
        limiter = self._rate_limiter()
        key = self._login_key(request, email)
        if await limiter.too_many_attempts(key, self._max_attempts):
            retry_after = max(1, await limiter.available_in(key))
            await dispatch(
                DomainEvent(
                    "Lockout",
                    {
                        "email": email.lower(),
                        "ip": getattr(request, "ip", None),
                        "retry_after": retry_after,
                    },
                )
            )
            raise ThrottleRequestsError(retry_after=retry_after)
        await limiter.hit(key, self._decay)

        user = await self.provider.retrieve_by_credentials(credentials)
        if user is None or not await self._validate_and_rehash(user, credentials):
            await dispatch(DomainEvent("Failed", {"email": email.lower()}))
            return False

        await self.login(request, user, remember=remember)
        await limiter.clear(key)  # success forgives the failure count
        return True

    async def _validate_and_rehash(self, user: Any, credentials: dict[str, Any]) -> bool:
        ok = await self.provider.validate_credentials(user, credentials)
        if ok:
            # Transparent digest upgrade — the provider owns the password
            # plumbing (and persists via save() when the user is an ORM row).
            await self.provider.rehash_password_if_required(user, credentials)
        return bool(ok)

    async def attempt_when(
        self,
        request: Any,
        credentials: dict[str, Any],
        callback: Any,
        *,
        remember: bool = False,
    ) -> bool:
        """attempt() behind an extra gate — ``callback(user)`` must be truthy."""
        user = await self.provider.retrieve_by_credentials(credentials)
        if user is None:
            return False
        outcome = callback(user)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        if not outcome:
            return False
        return await self.attempt(request, credentials, remember=remember)

    async def once(self, request: Any, credentials: dict[str, Any]) -> bool:
        """One-off credential check — no session write, no events."""
        user = await self.provider.retrieve_by_credentials(credentials)
        if user is None:
            return False
        return bool(await self.provider.validate_credentials(user, credentials))

    async def login_using_id(
        self, request: Any, identifier: Any, *, remember: bool = False
    ) -> bool:
        user = await self.provider.resolve(identifier)
        if user is None:
            return False
        await self.login(request, user, remember=remember)
        return True

    async def login(self, request: Any, user: Any, *, remember: bool = False) -> None:
        """Start a fresh authenticated session for ``user``.

        Session-fixation defense (OWASP): the session ID is regenerated
        (server-side sessions), everything planted in the pre-authentication
        session is discarded, and the CSRF token is rotated, so nothing
        observed before login authorizes anything after.
        """
        identifier = self.provider.identifier(user)
        self._authenticate_session(request, identifier)
        if remember:
            cookie = await remember_store().issue(identifier)
            _queue_remember_cookie(request, cookie)
        await dispatch(
            DomainEvent(
                "Login",
                {"user_id": identifier, "guard": "session", "remember": remember},
            )
        )

    def _authenticate_session(self, request: Any, identifier: Any) -> None:
        """Fresh session id, clean payload, rotated CSRF (fixation defense)."""
        from fastplace.auth.middleware import CSRF_SESSION_KEY, INTENDED_SESSION_KEY

        session = request.session
        regenerate = getattr(session, "regenerate", None)
        if callable(regenerate):
            # ServerSession: flag rotation — the middleware mints a fresh ID
            # and destroys the old row on the response. Plain dict sessions
            # (unit-test stand-ins) have nothing to rotate.
            regenerate()
        # The parked destination (auth middleware, spec §4.5) is the one
        # piece of pre-auth state that survives the boundary: login must
        # still resume the user's journey. It is a server-composed
        # same-origin path (request.full_path), never client-supplied, so
        # carrying it across the clear is not a fixation vector.
        intended = session.get(INTENDED_SESSION_KEY)
        session.clear()
        session[self.SESSION_KEY] = identifier
        session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
        if intended is not None:
            session[INTENDED_SESSION_KEY] = intended

    async def logout(self, request: Any) -> None:
        """End the session entirely — nothing of the authenticated state survives."""
        identifier = request.session.get(self.SESSION_KEY)
        cookie = self._remember_cookie(request)
        if cookie:
            await remember_store().revoke(cookie)
            _queue_remember_cookie(request, None)
        invalidate = getattr(request.session, "invalidate", None)
        if callable(invalidate):
            # ServerSession: destroy the backing row and expire the cookie —
            # a bare clear() would leave the store row revivable.
            invalidate()
        else:
            request.session.clear()
        await dispatch(DomainEvent("Logout", {"user_id": identifier}))

    async def user(self, request: Any) -> Any | None:
        identifier = request.session.get(self.SESSION_KEY)
        if identifier is not None:
            return await self.provider.resolve(identifier)

        # Remember-cookie fallback (spec §4.6): consume validates + rotates.
        cookie = self._remember_cookie(request)
        if not cookie:
            return None
        consumed = await remember_store().consume(cookie)
        if consumed is None:
            return None
        user_id, fresh_cookie = consumed
        user = await self.provider.resolve(user_id)
        if user is None:
            return None
        # The fallback IS a login for fixation purposes: fresh session id.
        self._authenticate_session(request, user_id)
        _queue_remember_cookie(request, fresh_cookie)
        scope = getattr(request, "scope", None)
        if scope is not None:
            scope[VIA_REMEMBER_SCOPE] = True
        return user

    async def via_remember(self, request: Any) -> bool:
        """True when this request authenticated via the remember cookie."""
        scope = getattr(request, "scope", None) or {}
        return bool(scope.get(VIA_REMEMBER_SCOPE))

    async def logout_other_devices(self, request: Any, current_password: str) -> bool:
        """Revoke every other session + every remember token (spec §4.6).

        Demands the current password: a stolen session cannot sweep the
        account's other devices without it.
        """
        identifier = request.session.get(self.SESSION_KEY)
        if identifier is None:
            return False
        user = await self.provider.resolve(identifier)
        if user is None:
            return False
        ok = await self.provider.validate_credentials(user, {"password": current_password})
        if not ok:
            return False

        scope = getattr(request, "scope", None) or {}
        store = scope.get(SESSION_STORE_SCOPE)
        if store is not None:
            await store.destroy_for_user(
                identifier,
                except_session_id=getattr(request.session, "session_id", None),
            )
        await remember_store().revoke_all_for_user(identifier)
        fresh = await remember_store().issue(identifier)
        _queue_remember_cookie(request, fresh)
        return True

    def _remember_cookie(self, request: Any) -> str | None:
        cookies = getattr(request, "cookies", None)
        if not cookies:
            return None
        get = cookies.get if callable(getattr(cookies, "get", None)) else None
        return get(REMEMBER_COOKIE_NAME) if get else None


def _queue_remember_cookie(request: Any, value: str | None) -> None:
    """Ask the auth middleware to set (value) or clear (None) the cookie.

    Guards never touch responses; the scope marker is flushed as a
    Set-Cookie header by ResolveUserMiddleware after the response is built.
    """
    scope = getattr(request, "scope", None)
    if scope is not None:
        scope[REMEMBER_COOKIE_SCOPE] = value


class TokenGuard:
    """Stateless guard — HS256 JWTs carried in ``Authorization: Bearer``."""

    def __init__(
        self,
        provider: UserProvider,
        secret: str,
        algorithm: str = "HS256",
        ttl: int = 3600,
        issuer: str = "fastplace",
    ) -> None:
        self.provider = provider
        self.algorithm = algorithm
        self.ttl = ttl
        self.issuer = issuer
        self.secret = secret
        if algorithm.startswith("HS") and len(secret) < 32:
            warnings.warn(
                f"APP_KEY is {len(secret)} bytes; HMAC-SHA256 keys below 32 bytes "
                "are below the RFC 7518 recommendation — generate a longer one.",
                stacklevel=2,
            )

    def issue_for(
        self, user: Any, claims: dict[str, Any] | None = None, ttl: int | None = None
    ) -> str:
        """Mint a token for ``user`` (subject = provider identifier)."""
        subject = str(self.provider.identifier(user))
        return self.issue(subject, claims=claims, ttl=ttl)

    #: Registered JWT claims issue() owns — callers cannot override these.
    _RESERVED_CLAIMS = frozenset({"sub", "iss", "iat", "exp"})

    def issue(
        self, subject: str, claims: dict[str, Any] | None = None, ttl: int | None = None
    ) -> str:
        from time import time

        for key in claims or {}:
            if key in self._RESERVED_CLAIMS:
                raise ValueError(f"claim '{key}' is managed by the guard — pass custom claims only")
        lifetime = self.ttl if ttl is None else ttl
        now = int(time())
        payload: dict[str, Any] = {
            "sub": subject,
            "iss": self.issuer,
            "iat": now,
            "exp": now + lifetime,  # negative ttl mints an already-expired token
        }
        payload.update(claims or {})
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    def decode(self, token: str) -> dict[str, Any]:
        """Verify signature + claims; raises ``jwt.PyJWTError`` on failure."""
        return jwt.decode(
            token,
            self.secret,
            algorithms=[self.algorithm],
            issuer=self.issuer,
            options={"require": ["sub", "iat", "exp"]},
        )

    def extract(self, request: Any) -> str | None:
        """Pull the raw bearer token off the request, if any."""
        header = request.header("Authorization")
        if not header:
            return None
        scheme, _, value = header.partition(" ")
        if scheme.lower() != BEARER_SCHEME.lower() or not value.strip():
            return None
        return value.strip()

    async def user(self, request: Any) -> Any | None:
        token = self.extract(request)
        if token is None:
            return None
        try:
            claims = self.decode(token)
        except jwt.PyJWTError:
            return None  # invalid/expired token ≙ unauthenticated, not a crash
        subject = claims["sub"]
        user = await self.provider.resolve(subject)
        if user is None and isinstance(subject, str) and subject.isdigit():
            # JWT subjects are strings; identifiers are often numeric.
            user = await self.provider.resolve(int(subject))
        return user


def guard(name: str | None = None) -> SessionGuard | TokenGuard:
    """Build the named (or default) guard from ``config/auth.py`` + env."""
    from fastplace.config import config

    resolved = name or config("AUTH_DEFAULT_GUARD", "session")
    guards_cfg = config("AUTH_GUARDS", {}) or {}
    entry = guards_cfg.get(resolved) or {}
    driver = entry.get("driver", resolved)
    provider = provider_from_config(config)

    if driver == "session":
        return SessionGuard(
            provider,
            limiter=RateLimiter(),
            max_attempts=int(config("AUTH_LOGIN_MAX_ATTEMPTS", default=5) or 5),
            decay=int(config("AUTH_LOGIN_DECAY", default=60) or 60),
        )
    if driver in ("jwt", "token"):
        secret = config("APP_KEY") or None
        if not secret:
            raise ConfigurationError(
                "APP_KEY is required to issue/verify JWTs — set it in .env "
                "(fastplace key:generate-style: python -c 'import secrets; "
                "print(secrets.token_urlsafe(48))')."
            )
        return TokenGuard(
            provider,
            secret=secret,
            algorithm=str(entry.get("algorithm", "HS256")),
            ttl=int(entry.get("ttl", 3600)),
            issuer=str(entry.get("issuer", "fastplace")),
        )
    raise ConfigurationError(
        f"Unknown auth guard driver '{driver}' (guard '{resolved}') — expected 'session' or 'jwt'."
    )
