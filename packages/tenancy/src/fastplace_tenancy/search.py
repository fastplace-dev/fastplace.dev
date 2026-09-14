"""Search isolation — a company filter over any SearchService.

The default database service already rides the ORM global scopes (Batch 4
unified the capability-gated searches onto the scope engine), so it is
tenant-correct by construction. Third-party services (Meilisearch,
Typesense, …) are not — :class:`TenantSearchService` wraps them and drops
result rows from other companies, logging the leak: a foreign row here
means the wrapped service is missing the tenant filter and that is a bug
worth surfacing, not silently papering over.

Register at boot::

    register_search_service(TenantSearchService(existing_service))
"""

from __future__ import annotations

import logging
from typing import Any

from fastplace_tenancy.context import require_company_context

logger = logging.getLogger("fastplace_tenancy.search")


class TenantSearchService:
    """SearchService wrapper filtering results to the bound company."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def search(self, query: str, *, model: Any | None = None, limit: int = 20) -> list[Any]:
        rows = await self._inner.search(query, model=model, limit=limit)
        company_id = require_company_context()
        kept: list[Any] = []
        for row in rows:
            row_company = getattr(row, "company_id", None)
            if row_company is None:
                # Shared content is a legal pattern — but on tenant tables
                # the column is non-nullable, so untagged usually means a
                # leaky index. Kept, and said out loud.
                logger.warning(
                    "search service %s returned row with no company while %s "
                    "was bound — kept as shared content; if these are tenant "
                    "rows, the service's index is missing company metadata",
                    type(self._inner).__name__,
                    company_id,
                )
                kept.append(row)
                continue
            if row_company == company_id:
                kept.append(row)
                continue
            # A foreign row in the results means the wrapped service is not
            # applying the tenant filter — record it, never serve it.
            logger.warning(
                "search service %s returned row of company %s while %s was bound "
                "— dropped; add a tenant filter to the service",
                type(self._inner).__name__,
                row_company,
                company_id,
            )
        return kept
