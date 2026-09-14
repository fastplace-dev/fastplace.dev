"""Seed the knowledge module — embeds only when a provider key is present."""

from __future__ import annotations

import os

_ITEMS: list[tuple[str, str]] = [
    (
        "Repositories own query construction",
        "Every filter, eager load, ordering, and aggregation lives in a "
        "repository method and runs as SQL — never as in-memory filtering.",
    ),
    (
        "Services are the module boundary",
        "Controllers, jobs, and other modules talk to a module's services "
        "only; repositories and models never leak across the boundary.",
    ),
]


async def run() -> None:
    from app.modules.knowledge.models.knowledge_item import KnowledgeItem
    from app.modules.knowledge.services.knowledge_service import KnowledgeService
    from fastplace.db import db

    # Idempotent by title, atomic as a whole (see projects_seeder.run); the
    # model query applies the soft-delete scope to the existence check.
    service = KnowledgeService()
    has_key = bool(os.environ.get("AI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    async with db.transaction():
        known = {item.title for item in await KnowledgeItem.query().get()}
        for title, content in _ITEMS:
            if title not in known:
                await service.ingest(title=title, content=content, embed_vector=has_key)
