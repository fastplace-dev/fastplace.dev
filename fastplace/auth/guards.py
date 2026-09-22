"""Auth guards — session (signed cookie) and token (JWT) strategies.

Both guards answer the same two questions: *log this user in / out* and
*who is the user on this request*. Guards resolve users through a
:class:`~fastplace.auth.providers.UserProvider`; they never query storage
themselves. Selection is configuration-driven (``config/auth.py``):

    AUTH_DEFAULT_GUARD = "session"
    AUTH_GUARDS = {"session": {"driver": "session"}, "token": {"driver": "jwt", ...}}
"""

from __future__ import annotations

import warnings
from typing import Any

import jwt

from fastplace.auth.providers import UserProvider, provider_from_config
from fastplace.errors import ConfigurationError

BEARER_SCHEME = "Bearer"


class SessionGuard:
    """Stateful guard backed by the signed-cookie session.

    The kernel installs Starlette's ``SessionMiddleware`` (itsdangerous
    signing), so ``request.session`` is a tamper-proof client-side store;
    the guard only writes the user identifier into it.
    """

    SESSION_KEY = "user_id"

    def __init__(self, provider: UserProvider) -> None:
        self.provider = provider

    async def login(self, request: Any, user: Any) -> None:
        """Start a fresh authenticated session for ``user``.

        Session-fixation defense (OWASP): everything planted in the
        pre-authentication session is discarded and the CSRF token is
        rotated, so nothing observed before login authorizes anything after.
        """
        import secrets

        from fastplace.auth.middleware import CSRF_SESSION_KEY

        request.session.clear()
        request.session[self.SESSION_KEY] = self.provider.identifier(user)
        request.session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)

    async def logout(self, request: Any) -> None:
        """End the session entirely — nothing of the authenticated state survives."""
        invalidate = getattr(request.session, "invalidate", None)
        if callable(invalidate):
            # ServerSession: destroy the backing row and expire the cookie —
            # a bare clear() would leave the store row revivable.
            invalidate()
        else:
            request.session.clear()

    async def user(self, request: Any) -> Any | None:
        identifier = request.session.get(self.SESSION_KEY)
        if identifier is None:
            return None
        return await self.provider.resolve(identifier)


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
        return SessionGuard(provider)
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
