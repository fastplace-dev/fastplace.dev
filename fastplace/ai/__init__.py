"""Fastplace AI engine — tools, agents, embeddings, vector stores.

Application code imports from here (``from fastplace.ai import Agent,
Tool, embed``); provider access routes through LiteLLM and structured
outputs validate through Instructor, so app code never imports either.
"""

from fastplace.ai.agent import Agent
from fastplace.ai.embeddings import embed, embed_many
from fastplace.ai.tool import Tool, ToolSpec, registered_tools, reset_tool_registry, tool_registry
from fastplace.ai.vectors import reset_vector_registry, vector_registry

__all__ = [
    "Agent",
    "Tool",
    "ToolSpec",
    "embed",
    "embed_many",
    "registered_tools",
    "reset_tool_registry",
    "reset_vector_registry",
    "tool_registry",
    "vector_registry",
]
