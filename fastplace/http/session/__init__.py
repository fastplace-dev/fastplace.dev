"""Server-side sessions — opaque-ID cookie backed by a pluggable store."""

from collections.abc import Callable
from typing import Any

from fastplace.http.session.base import SessionStore, StoredSession, session_lifetime
from fastplace.http.session.database import DatabaseSessionStore
from fastplace.http.session.memory import MemorySessionStore
from fastplace.http.session.redis_store import RedisSessionStore

__all__ = [
    "SessionStore",
    "StoredSession",
    "session_lifetime",
    "MemorySessionStore",
    "DatabaseSessionStore",
    "RedisSessionStore",
    "session_store",
]


def session_store(config_get: Callable[[str, Any], Any] | None = None) -> SessionStore:
    """Build the configured session store (spec §3.2 + Deviations §1).

    Explicit ``SESSION_DRIVER`` always wins; when unset, production apps get
    the revocable ``database`` driver and everything else (local dev, tests)
    gets ``memory`` — zero-config both ways.
    """
    from fastplace.errors import ConfigurationError

    def get(key: str, default: Any = None) -> Any:
        if config_get is not None:
            return config_get(key, default)
        from fastplace.config import config

        return config(key, default=default)

    driver = str(get("SESSION_DRIVER", default="") or "").strip().lower()
    if not driver:
        env = str(get("APP_ENV", default="local")).lower()
        driver = "database" if env == "production" else "memory"

    if driver == "memory":
        return MemorySessionStore()
    if driver == "database":
        from fastplace.http.session.database import DatabaseSessionStore

        return DatabaseSessionStore()
    if driver == "redis":
        from fastplace.http.session.redis_store import RedisSessionStore

        return RedisSessionStore()
    raise ConfigurationError(
        f"unknown SESSION_DRIVER {driver!r} — expected 'database', 'redis', or 'memory'"
    )
