"""New sample-app bridge pages — Knowledge index and Assistant chat."""

from __future__ import annotations

import pytest


@pytest.fixture()
async def seeded(sample_db):
    from app.modules.knowledge.services.knowledge_service import KnowledgeService

    service = KnowledgeService()
    await service.ingest(
        title="pgvector intro", content="embeddings in postgres", embed_vector=False
    )
    await service.ingest(title="mysql notes", content="charset gotchas", embed_vector=False)
    return service


BRIDGE = {"X-Fastplace-Request": "true"}


async def test_knowledge_page_lists_recent_items(seeded, sample_client):
    resp = await sample_client.get("/knowledge", headers=BRIDGE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["component"] == "Knowledge/Index"
    titles = [item["title"] for item in body["props"]["items"]]
    # newest first — the mysql note was ingested last
    assert titles == ["mysql notes", "pgvector intro"]


async def test_knowledge_page_filters_by_query(seeded, sample_client):
    resp = await sample_client.get("/knowledge", params={"q": "postgres"}, headers=BRIDGE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["props"]["q"] == "postgres"
    assert [item["title"] for item in body["props"]["items"]] == ["pgvector intro"]


async def test_knowledge_page_blank_query_lists_recent(seeded, sample_client):
    resp = await sample_client.get("/knowledge", params={"q": "   "}, headers=BRIDGE)
    assert resp.status_code == 200
    assert len(resp.json()["props"]["items"]) == 2


async def test_knowledge_page_rejects_oversized_query(seeded, sample_client):
    # The bridge edge enforces the same cost cap as the API edge — an
    # unbounded q reaches embedding providers / full-table scans.
    from app.http.controllers.knowledge_controller import MAX_QUERY_LENGTH

    resp = await sample_client.get(
        "/knowledge", params={"q": "a" * (MAX_QUERY_LENGTH + 1)}, headers=BRIDGE
    )
    assert resp.status_code == 422
    assert "q" in resp.json()["errors"]


async def test_assistant_page_renders_chat_component(sample_client):
    resp = await sample_client.get("/assistant", headers=BRIDGE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["component"] == "Assistant/Chat"


async def test_middleware_directory_ships_app_level_middleware():
    # Blueprint placement: app/http/middleware/ exists and config registers
    # from it — the framework no longer holds all middleware alone.
    from app.http.middleware.request_timing import RequestTimingMiddleware
    from config.app import MIDDLEWARE

    dotted = "app.http.middleware.request_timing.RequestTimingMiddleware"
    assert dotted in MIDDLEWARE
    assert RequestTimingMiddleware.__name__ == "RequestTimingMiddleware"


async def test_responses_carry_process_time_header(sample_client):
    resp = await sample_client.get("/knowledge", headers=BRIDGE)
    assert resp.status_code == 200
    assert float(resp.headers["x-process-time"]) >= 0.0
