"""APP_KEY-signed values with expiry — the signed-URL primitive (spec §4.11)."""

from __future__ import annotations

import hashlib
import hmac
import time

from fastplace.config import config
from fastplace.errors import ConfigurationError

#: Default time-to-live for signed values (seconds).
DEFAULT_TTL = 3600


def _secret() -> str:
    secret = str(config("APP_KEY", default="") or "")
    if not secret:
        raise ConfigurationError("APP_KEY is required to sign URLs.")
    # The raw string IS the HMAC secret (JWT precedent — no normalization).
    return secret


def sign(value: str, *, ttl: int = DEFAULT_TTL) -> tuple[str, int]:
    """Sign ``value`` until ``now + ttl``; returns (hex digest, expires)."""
    expires = int(time.time()) + ttl
    digest = hmac.new(
        _secret().encode("utf-8"), f"{value}|{expires}".encode(), hashlib.sha256
    ).hexdigest()
    return digest, expires


def verify(value: str, signature: str, expires: str) -> bool:
    """Constant-time check that ``signature`` covered ``value`` until ``expires``."""
    try:
        expires_at = int(expires)
    except (TypeError, ValueError):
        return False
    if expires_at < int(time.time()):
        return False
    expected = hmac.new(
        _secret().encode("utf-8"), f"{value}|{expires_at}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
