"""AI routes — the kernel mounts these under /ai (streamed agent endpoints)."""

from __future__ import annotations

from app.http.controllers.assistant_controller import AssistantController
from fastplace.http import Router

router = Router()

router.post("/assistant", AssistantController, "stream", name="ai.assistant")
