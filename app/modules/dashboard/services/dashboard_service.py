"""Dashboard service — assembles bridge props from other modules' services.

Cross-module rule (blueprint §4): the dashboard talks to the projects and
knowledge modules through their services only — never their repositories
or models. Everything arrives as the modules' typed DTOs, composed into
the ``DashboardOverview`` contract.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from app.modules.knowledge.services.knowledge_service import KnowledgeService
from app.modules.projects.services.projects_service import (
    ProjectResource,
    ProjectsService,
    ProjectStats,
)
from fastplace.config import config


class DashboardOverview(BaseModel):
    """The dashboard's response schema — API return annotation and bridge
    props come from the same typed contract (``-> DashboardOverview``)."""

    appName: str
    url: str | None = None
    stats: ProjectStats
    recent_projects: list[ProjectResource]
    knowledge_items: int


class DashboardService:
    """Business logic for the dashboard page (controllers stay thin)."""

    def __init__(self) -> None:
        self.projects = ProjectsService()
        self.knowledge = KnowledgeService()

    async def compose(self, *, url: str | None = None) -> DashboardOverview:
        stats, recent, knowledge_items = await asyncio.gather(
            self.projects.stats(),
            self.projects.list_projects(limit=5),
            self.knowledge.count_items(),
        )
        return DashboardOverview(
            appName=str(config("APP_NAME", default="Fastplace")),
            url=url,
            stats=stats,
            recent_projects=recent,
            knowledge_items=knowledge_items,
        )
