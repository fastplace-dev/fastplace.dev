"""The dogfood assistant agent — the app's /ai/assistant backend."""

from __future__ import annotations

from fastplace.ai import Agent
from fastplace.config import config

SYSTEM_PROMPT = (
    "You are the Fastplace assistant, a concise pair-programmer for the "
    "dogfood application. Answer in short, practical steps and prefer "
    "pointing at the relevant module (app/modules/*) over inventing code."
)


def assistant_agent() -> Agent:
    """Build the assistant on the configured chat model (AI_MODEL)."""
    return Agent(
        model=str(config("AI_MODEL", default="gpt-4o-mini")),
        system_prompt=SYSTEM_PROMPT,
    )
