"""Health controller — unified API liveness probe (/api/v1/health)."""

from __future__ import annotations

from fastplace.http import Controller, Request


class HealthController(Controller):
    async def index(self, request: Request):
        return {"status": "ok", "framework": "fastplace"}
