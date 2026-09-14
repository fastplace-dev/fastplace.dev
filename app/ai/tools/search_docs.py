"""Search the dogfood knowledge base from the assistant agent."""

from __future__ import annotations

from app.modules.knowledge.services.knowledge_service import KnowledgeService
from fastplace.ai import Tool

#: Matches the API/bridge search caps — same cost envelope everywhere.
MAX_HITS = 3
#: Knowledge items accept 50k chars; the model's context window does not.
EXCERPT_CHARS = 300


def _excerpt(content: str) -> str:
    """Bounded, clearly-sourced excerpt of one knowledge item.

    Retrieved text enters the transcript as ``role: "tool"`` output — an
    LLM prompt-injection surface. Labeling the source and truncating keeps
    tool output small and signals to the model that this is retrieved data,
    not instructions.
    """
    body = content[:EXCERPT_CHARS].rstrip()
    if len(content) > EXCERPT_CHARS:
        body += "…"
    return body


@Tool(description="Search internal documentation")
async def search_docs(query: str) -> list[str]:
    """Search the application's knowledge base and return the best matches.

    The agent gets short, factual excerpts it can cite — full tool outputs
    belong in the SSE stream, not the model's context window.
    """
    # Tools obey the same rule as controllers, jobs, and other modules:
    # module services only — never repositories, never models.
    hits = await KnowledgeService().search(query, limit=MAX_HITS)
    return [f"[knowledge item {hit.id}] {hit.title}: {_excerpt(hit.content)}" for hit in hits]
