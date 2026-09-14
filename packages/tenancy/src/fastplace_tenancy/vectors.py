"""Vector-store isolation — a company filter on similarity results.

``PgVectorStore`` rides ``Model.vector_search()``, which since Batch 4
applies the global-scope criteria — tenant-correct by construction. Custom
vector stores registered in ``app/ai/vectors/`` are not;
:class:`TenantVectorStore` wraps any :class:`~fastplace.ai.vectors.VectorStore`
and drops neighbors from other companies (logging the leak — see the search
wrapper for the reasoning).
"""

from __future__ import annotations

import logging
from typing import Any

from fastplace_tenancy.context import require_company_context

logger = logging.getLogger("fastplace_tenancy.vectors")


class TenantVectorStore:
    """VectorStore wrapper filtering neighbors to the bound company."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def search(self, model_cls: Any, embedding: list[float], limit: int = 10) -> list[Any]:
        rows = await self._inner.search(model_cls, embedding, limit=limit)
        company_id = require_company_context()
        kept: list[Any] = []
        for row in rows:
            row_company = getattr(row, "company_id", None)
            if row_company is None:
                # Shared content is a legal pattern — but on tenant tables
                # the column is non-nullable, so untagged usually means a
                # leaky index. Kept, and said out loud.
                logger.warning(
                    "vector store %s returned neighbor with no company while "
                    "%s was bound — kept as shared content; if these are tenant "
                    "rows, the store's index is missing company metadata",
                    type(self._inner).__name__,
                    company_id,
                )
                kept.append(row)
                continue
            if row_company == company_id:
                kept.append(row)
                continue
            logger.warning(
                "vector store %s returned neighbor of company %s while %s was "
                "bound — dropped; add company metadata to the store's query",
                type(self._inner).__name__,
                row_company,
                company_id,
            )
        return kept
