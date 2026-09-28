"""Absolute-URL building — APP_URL first, request host only when trusted."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from fastplace.config import config
from fastplace.errors import ConfigurationError
from fastplace.http.middleware import Middleware

if TYPE_CHECKING:
    from fastplace.http.request import Request


def build_absolute_url(path: str, *, request: Request | None = None) -> str:
    """Prefix ``path`` with a trustworthy origin.

    APP_URL (config/env) always wins; the request's own host is used only
    when APP_URL is empty AND the host appears in TRUSTED_HOSTS — a
    host-header forgery must never land inside an emailed link.
    """
    base = str(config("APP_URL", default="") or "").rstrip("/")
    if base:
        return f"{base}{path}"
    if request is not None:
        parsed = urlparse(str(request.url))
        if parsed.netloc and _host_is_trusted(parsed.hostname):
            return f"{parsed.scheme}://{parsed.netloc}{path}"
    raise ConfigurationError(
        "build_absolute_url needs APP_URL (or a request with a TRUSTED_HOSTS host)."
    )


def _host_is_trusted(hostname: str | None) -> bool:
    if not hostname:
        return False
    raw = config("TRUSTED_HOSTS", default=[])
    # A list default cannot be env-coerced, but a str (from any source) is
    # accepted as a comma-separated list.
    entries = (
        [entry.strip().lower() for entry in raw.split(",") if entry.strip()]
        if isinstance(raw, str)
        else [str(entry).strip().lower() for entry in (raw or [])]
    )
    return hostname.lower() in entries


def signed_url(path: str, *, ttl: int | None = None, request: Request | None = None) -> str:
    """An absolute, APP_KEY-signed URL: ``path?signature=...&expires=...``.

    The signature covers the route path only (never the query string — it
    would have to cover itself); send route identifiers as path segments.
    Routes accept these links by adding ``middleware=["signed"]`` — see
    :class:`SignedMiddleware`.
    """
    from fastplace.auth.signing import DEFAULT_TTL, sign

    signature, expires = sign(path, ttl=DEFAULT_TTL if ttl is None else ttl)
    return f"{build_absolute_url(path, request=request)}?signature={signature}&expires={expires}"


class SignedMiddleware(Middleware):
    """Route middleware for signed links (``middleware=["signed"]``).

    Validates the ``signature``/``expires`` query params against the request
    path (constant-time, expiry-checked — see ``fastplace.auth.signing``).
    Anything tampered, stale, or unsigned raises ``AuthorizationError`` and
    answers 403 through the kernel's JSON envelope.
    """

    async def handle(self, request: Request, call_next: Any) -> Any:
        from fastplace.auth.signing import verify
        from fastplace.errors import AuthorizationError

        path = request.full_path.partition("?")[0]
        signature = request.query("signature")
        expires = request.query("expires")
        if not signature or not expires or not verify(path, signature, expires):
            raise AuthorizationError()
        return await call_next(request)
