"""Server-side sessions — opaque-ID cookie backed by a pluggable store."""

from fastplace.http.session.base import SessionStore, StoredSession, session_lifetime
from fastplace.http.session.memory import MemorySessionStore

__all__ = ["SessionStore", "StoredSession", "session_lifetime", "MemorySessionStore"]
