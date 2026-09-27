"""Database capability registry — explicit feature availability per backend.

Fastplace never silently emulates unsupported functionality (blueprint §8):
applications check ``db.capabilities.supports_vector`` etc. and degrade or
fail explicitly.
"""

from __future__ import annotations

# Capability groups declared by driver (blueprint §8).
_RELATIONAL = {
    "transactions": True,
    "foreign_keys": True,
    "joins": True,
    "window_functions": True,
    "cte": True,
    "returning": True,
}

CAPABILITY_GROUPS: dict[str, dict[str, bool]] = {
    "sqlite": {
        **_RELATIONAL,
        "window_functions": False,  # partial support; conservative
        "returning": True,
        "full_text": False,
        "trigram": False,
        "json": True,
        "json_path": False,
        "vector": False,
        "similarity_search": False,
        "advisory_locks": False,
        "read_replicas": False,
        "row_level_security": False,
    },
    "postgresql": {
        **_RELATIONAL,
        "full_text": True,
        "trigram": True,  # via pg_trgm extension
        "json": True,
        "json_path": True,
        "vector": True,  # via pgvector extension
        "similarity_search": True,
        "advisory_locks": True,
        "read_replicas": True,
        "row_level_security": True,
    },
    "mysql": {
        **_RELATIONAL,
        "returning": False,
        # MySQL's MATCH...AGAINST full-text indexes exist, but the framework
        # ships only the PostgreSQL tsvector path — never claim a capability
        # the query builder cannot emit (blueprint §8: fail explicitly).
        "full_text": False,
        "trigram": False,
        "json": True,
        "json_path": True,
        "vector": False,
        "similarity_search": False,
        "advisory_locks": True,
        "read_replicas": True,
        "row_level_security": False,  # never claim PostgreSQL-style RLS
    },
    "mongodb": {
        "transactions": True,
        "foreign_keys": False,
        "joins": False,
        "window_functions": False,
        "cte": False,
        "returning": False,
        "full_text": True,
        "trigram": False,
        "json": True,
        "json_path": True,
        "vector": True,  # Atlas Vector Search
        "similarity_search": True,
        "advisory_locks": False,
        "read_replicas": True,
        "row_level_security": False,
    },
}

_ALIASES = {"postgres": "postgresql", "sqlite3": "sqlite", "mariadb": "mysql"}


def normalize_driver(driver: str) -> str:
    """Canonical family name for a driver alias (``postgres`` → ``postgresql``)."""
    lowered = str(driver).strip().lower()
    return _ALIASES.get(lowered, lowered)


def driver_from_url(url: str) -> str:
    """Derive the driver name from a connection URL scheme.

    Case-insensitive — URLs pasted from dashboards arrive in any casing.
    """
    scheme = url.split(":", 1)[0].split("+", 1)[0].lower()
    return normalize_driver(scheme)


class Capabilities:
    """Attribute-style capability access: ``db.capabilities.supports_vector``."""

    def __init__(self, driver: str) -> None:
        self.driver = driver
        self._groups = CAPABILITY_GROUPS.get(driver, dict(_RELATIONAL))

    def supports(self, capability: str) -> bool:
        return bool(self._groups.get(capability, False))

    def __getattr__(self, name: str) -> bool:
        if name.startswith("supports_"):
            return self.supports(name.removeprefix("supports_"))
        raise AttributeError(name)

    def __repr__(self) -> str:  # pragma: no cover — debug convenience
        return f"<Capabilities driver={self.driver!r} {self._groups}>"
