"""Dashboard controller — the dogfood bridge page (blueprint §12)."""

from __future__ import annotations

from app.modules.dashboard.services.dashboard_service import DashboardService
from fastplace.http import Controller, Request, render


class DashboardController(Controller):
    service = DashboardService()

    async def index(self, request: Request):
        props = await self.service.compose(url=request.full_path)
        return render(request, component="Dashboard/Index", props=props)
