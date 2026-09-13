"""Unified JSON API routes — the kernel mounts these under /api/v1."""

from __future__ import annotations

from app.http.controllers.health_controller import HealthController
from fastplace.http import Router

router = Router()

router.get("/health", HealthController, "index", name="api.health")
