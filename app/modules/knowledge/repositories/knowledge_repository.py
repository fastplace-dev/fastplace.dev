"""Knowledge repository — ingestion and capability-aware similarity search."""

from __future__ import annotations

from app.modules.knowledge.models.knowledge_item import KnowledgeItem
from fastplace.db import db
from fastplace.orm.session import run_read


class KnowledgeRepository:
    """All KnowledgeItem query construction."""

    async def create(
        self, *, title: str, content: str, embedding: list[float] | None
    ) -> KnowledgeItem:
        return await KnowledgeItem.create(title=title, content=content, embedding=embedding)

    async def count_all(self) -> int:
        return await KnowledgeItem.query().count()

    async def search_like(self, needle: str, *, limit: int) -> list[KnowledgeItem]:
        """DB-side substring fallback for backends without vector/FTS support."""
        pattern = f"%{needle}%"
        stmt = (
            KnowledgeItem.query()
            .where(KnowledgeItem.title.ilike(pattern) | KnowledgeItem.content.ilike(pattern))
            .limit(limit)
            ._statement()
        )
        result = await run_read(stmt)
        return list(result.scalars().all())

    async def search_vector(self, embedding: list[float], *, limit: int) -> list[KnowledgeItem]:
        """Cosine-similarity search on a vector-capable backend (pgvector)."""
        if not db.capabilities.supports_vector:
            from fastplace.errors import SearchCapabilityMissing

            raise SearchCapabilityMissing(
                "vector search requires a vector-capable backend (PostgreSQL + pgvector)"
            )
        return list(await KnowledgeItem.vector_search(embedding, limit=limit))
