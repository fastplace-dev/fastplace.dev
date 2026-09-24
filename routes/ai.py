"""AI routes — the kernel mounts these under /ai (streamed agent endpoints)."""

from __future__ import annotations

from app.http.controllers.assistant_controller import AssistantController
from fastplace.http import Router

router = Router()

# The agent drives a provider-backed LLM per request — authenticated and
# throttled so anonymous or runaway traffic cannot spend real money (audit T6).
router.post(
    "/assistant",
    AssistantController,
    "stream",
    name="ai.assistant",
    middleware=["auth", "throttle:10,60"],
)
