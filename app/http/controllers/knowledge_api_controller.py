"""Knowledge JSON API — ingest and capability-gated search."""

from __future__ import annotations

from app.http.requests.ingest_knowledge_request import IngestKnowledgeRequest
from app.modules.knowledge.services.knowledge_service import KnowledgeService
from fastplace.errors import ValidationError
from fastplace.http import Controller, Json, Request

#: Longest accepted search phrase — every query is embedded on vector
#: backends, so an unbounded `q` is a cost/DoS vector.
MAX_QUERY_LENGTH = 500


class KnowledgeApiController(Controller):
    service = KnowledgeService()

    async def store(self, request: Request):
        data = await request.validate(IngestKnowledgeRequest)
        item = await self.service.ingest(title=data.title, content=data.content)
        return Json(item, status_code=201)

    async def search(self, request: Request):
        query = str(request.query("q") or "")
        if len(query) > MAX_QUERY_LENGTH:
            raise ValidationError(
                "The given data was invalid.",
                errors={"q": [f"must be at most {MAX_QUERY_LENGTH} characters"]},
            )
        hits = await self.service.search(query)
        return {"data": hits}
