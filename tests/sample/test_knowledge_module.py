"""Knowledge module — VectorField ingestion and capability-gated search."""

from __future__ import annotations

import pytest

from fastplace.errors import ValidationError


@pytest.fixture()
def service():
    from app.modules.knowledge.services.knowledge_service import KnowledgeService

    return KnowledgeService()


async def _stored(item) -> object:
    """The persisted row — the vector lives here, not on the public DTO."""
    from app.modules.knowledge.models.knowledge_item import KnowledgeItem

    return await KnowledgeItem.find(item.id)


async def test_ingest_embeds_content_and_stores_it(service, sample_db, embedding_seam):
    item = await service.ingest(title="ORM design", content="Repositories own query construction.")

    assert item.title == "ORM design"
    stored = await _stored(item)
    assert stored.embedding == [0.001] * 1536  # persisted alongside the text
    assert embedding_seam and "Repositories" in embedding_seam[0]["input"][0]


async def test_public_resources_never_carry_the_vector(service, sample_db, embedding_seam):
    """The embedding stays module-internal: no DTO dump exposes it."""
    item = await service.ingest(title="vector", content="kept internal")
    assert "embedding" not in item.model_dump()

    hits = await service.search("internal")
    assert hits and "embedding" not in hits[0].model_dump()


async def test_ingest_can_skip_embedding_when_no_provider(service, sample_db):
    # Seeding without an API key must still work — the vector can be
    # backfilled later by a job.
    item = await service.ingest(title="Notes", content="plain text", embed_vector=False)
    assert (await _stored(item)).embedding is None


async def test_ingest_rejects_empty_content(service, sample_db):
    with pytest.raises(ValidationError):
        await service.ingest(title="", content="   ")


async def test_search_filters_in_the_database_like_fallback(service, sample_db):
    # sqlite has no vector index — the service falls back to a DB-side
    # LIKE filter over title/content instead of loading every row.
    await service.ingest(
        title="pgvector intro", content="embeddings in postgres", embed_vector=False
    )
    await service.ingest(title="mysql notes", content="charset gotchas", embed_vector=False)

    hits = await service.search("postgres")
    assert [h.title for h in hits] == ["pgvector intro"]


async def test_search_accepts_a_limit(service, sample_db):
    for n in range(4):
        await service.ingest(title=f"note {n}", content="quantum", embed_vector=False)
    hits = await service.search("quantum", limit=2)
    assert len(hits) == 2


async def test_ingest_degrades_gracefully_without_an_embedding_provider(
    service, sample_db, monkeypatch
):
    """A missing provider key must degrade to an unvectored item, not a 500."""
    import fastplace.ai.embeddings as embeddings

    async def no_provider(*, model, input):
        raise RuntimeError("no API key configured for the embedding model")

    monkeypatch.setattr(embeddings, "_embedding_fn", no_provider)

    item = await service.ingest(title="Notes", content="plain text")
    assert item.title == "Notes"
    # stored unvectored; search falls back to LIKE
    assert (await _stored(item)).embedding is None


async def test_ingest_rejects_mismatched_embedding_dimensions(service, sample_db, monkeypatch):
    """A model/column width mismatch is a config error — fail loudly instead
    of persisting a vector the backend will refuse."""
    import fastplace.ai.embeddings as embeddings

    async def wrong_width(*, model, input):
        return [[0.1, 0.2, 0.3] for _ in input]

    monkeypatch.setattr(embeddings, "_embedding_fn", wrong_width)

    with pytest.raises(ValidationError, match="(?i)dimension"):
        await service.ingest(title="Bad width", content="text")
