"""KnowledgeItem — long-lived text with an embedding (blueprint VectorField)."""

from __future__ import annotations

from fastplace.orm import Field, Model, VectorField

#: text-embedding-3-small output width — the framework's default embedding.
EMBEDDING_DIMENSIONS = 1536


class KnowledgeItem(Model):
    __tablename__ = "knowledge_items"

    id: int = Field(primary_key=True)
    title: str
    # Bodies are long-form documents — TEXT, not VARCHAR(255).
    content: str = Field(text=True)
    embedding: list[float] | None = VectorField(dimensions=EMBEDDING_DIMENSIONS)
