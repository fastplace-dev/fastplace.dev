"""Dashboard controller — the sample-app bridge page (blueprint §12)."""

from __future__ import annotations

from app.modules.dashboard.services.dashboard_service import DashboardService
from fastplace.http import Controller, Request, render


class DashboardController(Controller):
    service = DashboardService()

    async def index(self, request: Request):
        overview = await self.service.compose(url=request.full_path)
        # Bridge props are JSON payloads — mode="json" keeps dates and other
        # non-JSON natives stringified.
        return render(request, component="Dashboard/Index", props=overview.model_dump(mode="json"))
