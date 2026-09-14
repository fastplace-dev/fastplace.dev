"""The sample app's assistant agent — the app's /ai/assistant backend."""

from __future__ import annotations

from app.ai.tools.search_docs import search_docs
from fastplace.ai import Agent
from fastplace.config import config

SYSTEM_PROMPT = (
    "You are the Fastplace assistant, a concise pair-programmer for the "
    "sample application. Answer in short, practical steps and prefer "
    "pointing at the relevant module (app/modules/*) over inventing code. "
    "Use the search_docs tool before answering questions about the project's "
    "knowledge base."
)


def assistant_agent() -> Agent:
    """Build the assistant on the configured chat model (AI_MODEL)."""
    return Agent(
        model=str(config("AI_MODEL", default="gpt-4o-mini")),
        system_prompt=SYSTEM_PROMPT,
        tools=[search_docs],
    )
