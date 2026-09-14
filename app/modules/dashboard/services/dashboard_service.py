"""Dashboard service — assembles bridge props from other modules' services.

Cross-module rule (blueprint §4): the dashboard talks to the projects and
knowledge modules through their services only — never their repositories
or models. Everything arrives as plain dict DTOs.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.modules.knowledge.services.knowledge_service import KnowledgeService
from app.modules.projects.services.projects_service import ProjectsService
from fastplace.config import config


class DashboardService:
    """Business logic for the dashboard page (controllers stay thin)."""

    def __init__(self) -> None:
        self.projects = ProjectsService()
        self.knowledge = KnowledgeService()

    async def compose(self, *, url: str | None = None) -> dict[str, Any]:
        stats, recent, knowledge_items = await asyncio.gather(
            self.projects.stats(),
            self.projects.list_projects(limit=5),
            self.knowledge.count_items(),
        )
        return {
            "appName": str(config("APP_NAME", default="Fastplace")),
            "url": url,
            "stats": stats,
            "recent_projects": recent,
            "knowledge_items": knowledge_items,
        }
