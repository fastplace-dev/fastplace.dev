"""SearchService abstraction — capability gating, delegation, pluggability (T6.4).

The default service is the database itself (PostgreSQL FTS through
``Model.full_text_search``); the registry exists so applications can swap
in Meilisearch/Typesense/etc. without touching call sites.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _default_registry():
    from fastplace.search import reset_search_service

    reset_search_service()
    yield
    reset_search_service()


class RecordingModel:
    """Fake repository model — records the FTS call without a database."""

    calls: list[tuple[str, dict]] = []

    @classmethod
    async def full_text_search(cls, query: str, *, limit: int = 20):
        cls.calls.append((query, {"limit": limit}))
        return [f"hit:{query}"]


@pytest.fixture()
def recorded():
    RecordingModel.calls = []
    return RecordingModel


def test_database_search_service_satisfies_the_protocol():
    from fastplace.orm.capabilities import Capabilities
    from fastplace.search import DatabaseSearchService, SearchService

    service = DatabaseSearchService(capabilities=Capabilities("postgresql"))
    assert isinstance(service, SearchService)


async def test_search_delegates_to_model_full_text_search(recorded):
    from fastplace.orm.capabilities import Capabilities
    from fastplace.search import DatabaseSearchService

    service = DatabaseSearchService(capabilities=Capabilities("postgresql"))
    hits = await service.search("embedded database", model=recorded, limit=5)

    assert hits == ["hit:embedded database"]
    assert recorded.calls == [("embedded database", {"limit": 5})]


async def test_backends_without_fts_fail_with_a_clear_error():
    from fastplace.orm.capabilities import Capabilities
    from fastplace.search import DatabaseSearchService, SearchNotSupported

    service = DatabaseSearchService(capabilities=Capabilities("sqlite"))
    with pytest.raises(SearchNotSupported, match="sqlite"):
        await service.search("anything", model=RecordingModel)


async def test_model_is_required(recorded):
    from fastplace.orm.capabilities import Capabilities
    from fastplace.search import DatabaseSearchService

    service = DatabaseSearchService(capabilities=Capabilities("postgresql"))
    with pytest.raises(ValueError, match="model"):
        await service.search("anything")


def test_registry_defaults_to_the_database_service():
    from fastplace.search import DatabaseSearchService, get_search_service

    assert isinstance(get_search_service(), DatabaseSearchService)


def test_registry_accepts_custom_services_and_resets():
    from fastplace.search import (
        DatabaseSearchService,
        get_search_service,
        register_search_service,
    )

    class MeiliService:
        async def search(self, query: str, *, model=None, limit: int = 20):
            return ["meili"]

    register_search_service(MeiliService())
    assert not isinstance(get_search_service(), DatabaseSearchService)

    from fastplace.search import reset_search_service

    reset_search_service()
    assert isinstance(get_search_service(), DatabaseSearchService)


def test_registry_rejects_objects_without_search():
    from fastplace.search import register_search_service

    with pytest.raises(TypeError, match="search"):
        register_search_service(object())


async def test_registered_service_is_what_callers_get():
    from fastplace.search import get_search_service, register_search_service

    class Echo:
        async def search(self, query: str, *, model=None, limit: int = 20):
            return [{"query": query, "limit": limit}]

    register_search_service(Echo())
    assert await get_search_service().search("hi", limit=2) == [{"query": "hi", "limit": 2}]
