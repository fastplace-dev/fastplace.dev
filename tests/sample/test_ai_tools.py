"""Sample-app AI tools — app/ai/tools surfaces real agent tools (blueprint §9)."""

from __future__ import annotations

import pytest


@pytest.fixture()
def registry():
    """The shared registry, empty at test start (conftest purges per test)."""
    from fastplace.ai import tool_registry

    tool_registry.clear()
    yield tool_registry
    tool_registry.clear()


async def test_search_docs_tool_registered_from_app_package(registry, sample_db):
    # Importing the module registers the tool — the registry auto-import
    # from app/ai/tools/ mirrors what make:agent scaffolds.
    import app.ai.tools  # noqa: F401 — side-effectful import
    from app.ai.tools.search_docs import search_docs

    assert "search_docs" in registry
    assert registry["search_docs"].fn is search_docs


async def test_search_docs_formats_hits_via_knowledge_service(sample_db):
    from app.ai.tools.search_docs import search_docs
    from app.modules.knowledge.services.knowledge_service import KnowledgeService

    await KnowledgeService().ingest(
        title="pgvector intro", content="embeddings in postgres", embed_vector=False
    )
    await KnowledgeService().ingest(
        title="mysql notes", content="charset gotchas", embed_vector=False
    )

    hits = await search_docs("postgres")
    # Each hit is labeled with its source id and kept as a bounded excerpt —
    # retrieved knowledge enters the transcript as data, never as raw text
    # that could impersonate instructions.
    assert len(hits) == 1
    assert hits[0].startswith("[knowledge item ")
    assert "pgvector intro" in hits[0]
    assert "embeddings in postgres" in hits[0]


async def test_search_docs_caps_results_at_three(sample_db):
    from app.ai.tools.search_docs import search_docs
    from app.modules.knowledge.services.knowledge_service import KnowledgeService

    for n in range(5):
        await KnowledgeService().ingest(
            title=f"quantum note {n}", content="quantum", embed_vector=False
        )

    hits = await search_docs("quantum")
    assert len(hits) == 3


async def test_search_docs_truncates_huge_content_to_a_bounded_excerpt(sample_db):
    # Ingested content can be 50k chars; the tool must not flood the
    # model's context with it.
    from app.ai.tools.search_docs import EXCERPT_CHARS, search_docs
    from app.modules.knowledge.services.knowledge_service import KnowledgeService

    await KnowledgeService().ingest(
        title="huge", content="needle " + "x" * 50_000, embed_vector=False
    )

    hits = await search_docs("needle")
    assert len(hits) == 1
    assert len(hits[0]) <= EXCERPT_CHARS + 80  # excerpt + label overhead
    assert hits[0].endswith("…")


async def test_assistant_agent_carries_the_tool():
    from app.ai.agents.assistant import assistant_agent

    agent = assistant_agent()
    assert [spec.name for spec in agent.tools] == ["search_docs"]
