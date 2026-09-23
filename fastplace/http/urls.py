"""Absolute-URL building — APP_URL first, request host only when trusted."""

from __future__ import annotations

from urllib.parse import urlparse

from fastplace.config import config
from fastplace.errors import ConfigurationError
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
