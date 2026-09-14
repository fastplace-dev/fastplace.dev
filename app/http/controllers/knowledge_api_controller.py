"""Knowledge JSON API — ingest and capability-gated search."""

from __future__ import annotations

from pydantic import BaseModel

from app.http.requests.ingest_knowledge_request import IngestKnowledgeRequest
from app.modules.knowledge.services.knowledge_service import (
    KnowledgeItemResource,
    KnowledgeService,
)
from fastplace.errors import ValidationError
from fastplace.http import Controller, Json, Request

#: Longest accepted search phrase — every query is embedded on vector
#: backends, so an unbounded `q` is a cost/DoS vector.
MAX_QUERY_LENGTH = 500


class KnowledgeSearchPage(BaseModel):
    """The `/api/v1/knowledge/search` response contract."""

    data: list[KnowledgeItemResource]


class KnowledgeApiController(Controller):
    service = KnowledgeService()

    async def store(self, request: Request) -> KnowledgeItemResource:
        data = await request.validate(IngestKnowledgeRequest)
        item = await self.service.ingest(title=data.title, content=data.content)
        return Json(item.model_dump(mode="json"), status_code=201)

    async def search(self, request: Request) -> KnowledgeSearchPage:
        query = str(request.query("q") or "")
        if len(query) > MAX_QUERY_LENGTH:
            raise ValidationError(
                "The given data was invalid.",
                errors={"q": [f"must be at most {MAX_QUERY_LENGTH} characters"]},
            )
        hits = await self.service.search(query)
        return KnowledgeSearchPage(data=hits)
