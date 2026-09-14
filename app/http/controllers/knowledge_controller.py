"""Knowledge bridge page — browse and search the knowledge base."""

from __future__ import annotations

from app.modules.knowledge.services.knowledge_service import (
    KnowledgeItemResource,
    KnowledgeService,
)
from fastplace.errors import ValidationError
from fastplace.http import Controller, Request, render

#: Longest accepted search phrase — every query may hit an embedding
#: provider on vector backends, so an unbounded `q` is a cost/DoS vector.
#: The API edge (knowledge_api_controller) enforces the same cap.
MAX_QUERY_LENGTH = 500


class KnowledgeController(Controller):
    service = KnowledgeService()

    async def index(self, request: Request):
        query = str(request.query("q") or "").strip()
        if len(query) > MAX_QUERY_LENGTH:
            raise ValidationError(
                "The given data was invalid.",
                errors={"q": [f"must be at most {MAX_QUERY_LENGTH} characters"]},
            )
        if query:
            items = await self.service.search(query, limit=20)
        else:
            # Blank search still lists the newest entries — the page is
            # useful before anyone types.
            items = await self.service.recent_items(limit=20)
        props: dict = {
            "q": query,
            "items": [self._resource(item) for item in items],
        }
        return render(request, component="Knowledge/Index", props=props)

    @staticmethod
    def _resource(item: KnowledgeItemResource) -> dict:
        # No content on the listing — excerpts stay on the detail surface.
        return {"id": item.id, "title": item.title}
