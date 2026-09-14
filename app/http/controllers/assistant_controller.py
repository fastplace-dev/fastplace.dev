"""Assistant controller — validates, delegates to the agent, streams SSE."""

from __future__ import annotations

from app.http.requests.assistant_request import AssistantRequest
from fastplace.http import Controller, Request


class AssistantController(Controller):
    async def stream(self, request: Request):
        data = await request.validate(AssistantRequest)
        # Factory resolved per request — tests swap the seam without
        # touching provider configuration. History leaves the DTO layer as
        # plain dicts, exactly the shape the agent loop consumes.
        from app.ai.agents.assistant import assistant_agent

        history = [turn.model_dump() for turn in data.history] if data.history else None
        return assistant_agent().stream_response(data.message, history=history)
