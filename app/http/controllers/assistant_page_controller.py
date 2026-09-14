"""Assistant page — the bridge shell for the SSE chat component."""

from __future__ import annotations

from fastplace.http import Controller, Request, render


class AssistantPageController(Controller):
    async def index(self, request: Request):
        # The chat state lives client-side (useAIStream); the page payload
        # only needs the endpoint the hook posts to.
        return render(request, component="Assistant/Chat", props={"endpoint": "/ai/assistant"})
