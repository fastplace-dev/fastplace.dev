"""Knowledge service — ingestion with embeddings, capability-gated search.

The module's only public surface: controllers and other modules call these
methods, never the repository or model below.
"""

from __future__ import annotations

from typing import Any

from app.modules.knowledge.models.knowledge_item import EMBEDDING_DIMENSIONS, KnowledgeItem
from app.modules.knowledge.repositories.knowledge_repository import KnowledgeRepository
from fastplace.db import db
from fastplace.errors import ValidationError


def item_resource(item: KnowledgeItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "content": item.content,
        "embedding": item.embedding,
    }


class KnowledgeService:
    """Ingest text into the knowledge base and search it back out."""

    def __init__(self) -> None:
        self.items = KnowledgeRepository()

    async def ingest(
        self,
        *,
        title: str,
        content: str,
        embed_vector: bool = True,
    ) -> dict[str, Any]:
        if not (title or "").strip() and not (content or "").strip():
            raise ValidationError("a knowledge item needs a title or content")
        embedding: list[float] | None = None
        if embed_vector:
            from fastplace.ai import embed

            try:
                embedding = await embed(f"{title}\n\n{content}")
            except Exception:  # noqa: BLE001 — no provider/key: store unvectored
                embedding = None
            else:
                if len(embedding) != EMBEDDING_DIMENSIONS:
                    # A model/column width mismatch would poison the vector
                    # index — fail loudly instead of persisting it.
                    raise ValidationError(
                        "embedding dimensions mismatch: model returned "
                        f"{len(embedding)}, the column declares {EMBEDDING_DIMENSIONS} "
                        "(check AI_EMBEDDING_MODEL)"
                    )
        item = await self.items.create(
            title=(title or "").strip(), content=(content or "").strip(), embedding=embedding
        )
        return item_resource(item)

    async def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        # Vector similarity when the backend supports it; the FTS service
        # handles Postgres full-text; everything else filters in the DB with
        # a substring match (never row-by-row in application memory).
        if db.capabilities.supports_vector:
            from fastplace.ai import embed

            try:
                vector = await embed(query)
            except Exception:  # noqa: BLE001 — provider unavailable: degrade, never 500
                rows = await self.items.search_like(query, limit=limit)
            else:
                # Only the provider call is guarded — a database error in the
                # vector query itself must surface, not silently degrade.
                rows = await self.items.search_vector(vector, limit=limit)
        else:
            rows = await self.items.search_like(query, limit=limit)
        return [item_resource(row) for row in rows]

    async def count_items(self) -> int:
        return await self.items.count_all()
