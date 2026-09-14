"""Search abstraction — pluggable ``SearchService`` with a database default.

The framework ships no third-party search dependency: the default service
is the relational database itself, using PostgreSQL full-text search
through ``Model.full_text_search()`` behind the ``supports_full_text``
capability flag. Applications register their own service (Meilisearch,
Typesense, an embedding-backed store) and every call site keeps working.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SearchService(Protocol):
    """Anything that can answer a free-text query with model-shaped rows."""

    async def search(
        self, query: str, *, model: SearchableModel | None = None, limit: int = 20
    ) -> list[Any]: ...


@runtime_checkable
class SearchableModel(Protocol):
    """A repository model the default service can search (relational ``Model``)."""

    @classmethod
    async def full_text_search(cls, query: str, *, limit: int = 20) -> list[Any]: ...


class SearchNotSupported(RuntimeError):
    """Raised when the active backend cannot serve full-text queries."""


class DatabaseSearchService:
    """Default service — PostgreSQL FTS via ``Model.full_text_search()``."""

    def __init__(self, capabilities: Any | None = None, connection: str = "default") -> None:
        self._connection = connection
        self._capabilities = capabilities

    def _caps(self) -> Any:
        if self._capabilities is not None:
            return self._capabilities
        from fastplace.db import db

        return db.capabilities

    async def search(
        self, query: str, *, model: SearchableModel | None = None, limit: int = 20
    ) -> list[Any]:
        if model is None:
            raise ValueError("model is required — pass the repository model to search")
        caps = self._caps()
        if not caps.supports_full_text:
            raise SearchNotSupported(
                f"backend {caps.driver!r} has no full-text search — use "
                "PostgreSQL or register a custom SearchService "
                "(fastplace.search.register_search_service)"
            )
        return await model.full_text_search(query, limit=limit)


_service: SearchService | None = None


def get_search_service() -> SearchService:
    """The registered service — ``DatabaseSearchService`` until overridden."""
    global _service
    if _service is None:
        _service = DatabaseSearchService()
    return _service


def register_search_service(service: SearchService) -> None:
    """Swap the process-wide search implementation (applications, tests)."""
    if not callable(getattr(service, "search", None)):
        raise TypeError(
            "search service must implement async search(query, *, model=None, limit=20)"
        )
    global _service
    _service = service


def reset_search_service() -> None:
    """Drop any override — back to the database-backed default."""
    global _service
    _service = None
