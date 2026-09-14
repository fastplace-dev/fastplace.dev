"""Dashboard JSON API — the same overview DTO the bridge page renders."""

from __future__ import annotations

from app.modules.dashboard.services.dashboard_service import (
    DashboardOverview,
    DashboardService,
)
from fastplace.http import Controller, Request


class DashboardApiController(Controller):
    service = DashboardService()

    async def index(self, request: Request) -> DashboardOverview:
        return await self.service.compose(url=request.full_path)
