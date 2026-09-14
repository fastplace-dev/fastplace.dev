"""Unified JSON API routes — the kernel mounts these under /api/v1."""

from __future__ import annotations

from app.http.controllers.dashboard_api_controller import DashboardApiController
from app.http.controllers.health_controller import HealthController
from app.http.controllers.knowledge_api_controller import KnowledgeApiController
from app.http.controllers.projects_api_controller import ProjectsApiController
from fastplace.http import Router

router = Router()

router.get("/health", HealthController, "index", name="api.health")
router.get("/dashboard", DashboardApiController, "index", name="api.dashboard")

router.get("/projects", ProjectsApiController, "index", name="api.projects.index")
router.post("/projects", ProjectsApiController, "store", name="api.projects.store")
router.get("/projects/{id}", ProjectsApiController, "show", name="api.projects.show")
router.post("/projects/{id}/tasks", ProjectsApiController, "store_task", name="api.projects.tasks")
router.patch("/tasks/{id}/toggle", ProjectsApiController, "toggle_task", name="api.tasks.toggle")

router.post("/knowledge", KnowledgeApiController, "store", name="api.knowledge.store")
router.get("/knowledge/search", KnowledgeApiController, "search", name="api.knowledge.search")
