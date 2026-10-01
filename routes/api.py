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
# Mirrors of the web writes: authenticated too (audit T6). Reads stay the
# public demo surface.
router.post(
    "/projects", ProjectsApiController, "store", name="api.projects.store", middleware=["auth"]
)
router.get("/projects/{id}", ProjectsApiController, "show", name="api.projects.show")
router.post(
    "/projects/{id}/tasks",
    ProjectsApiController,
    "store_task",
    name="api.projects.tasks",
    middleware=["auth"],
)
router.patch(
    "/tasks/{id}/toggle",
    ProjectsApiController,
    "toggle_task",
    name="api.tasks.toggle",
    middleware=["auth"],
)

# Knowledge ingest/search spend a provider-backed embedding call per
# request (audit T6) — the assistant route's guardrails, mirrored: an
# anonymous visitor must not spend real money or mutate the demo corpus.
router.post(
    "/knowledge",
    KnowledgeApiController,
    "store",
    name="api.knowledge.store",
    middleware=["auth", "throttle:10,60"],
)
router.get(
    "/knowledge/search",
    KnowledgeApiController,
    "search",
    name="api.knowledge.search",
    middleware=["auth", "throttle:10,60"],
)
