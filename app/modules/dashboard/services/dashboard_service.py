"""Dashboard service — assembles bridge props for the dashboard page."""

from __future__ import annotations

from typing import Any

from fastplace.config import config


class DashboardService:
    """Business logic for the dashboard page (controllers stay thin)."""

    async def compose(self, *, url: str) -> dict[str, Any]:
        return {
            "appName": str(config("APP_NAME", default="Fastplace")),
            "url": url,
            "projects": await self._projects(),
        }

    async def _projects(self) -> list[dict[str, Any]]:
        # The projects module lands in Phase 7; until then the dashboard
        # renders an honest empty state.
        return []
