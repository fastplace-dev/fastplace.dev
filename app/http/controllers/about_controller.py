"""About controller — second sample-app bridge page for E2E navigation."""

from __future__ import annotations

from app.modules.about.services.about_service import AboutService
from fastplace.http import Controller, Request, render


class AboutController(Controller):
    service = AboutService()

    async def index(self, request: Request):
        props = await self.service.compose(url=request.full_path)
        return render(request, component="About/Index", props=props)
