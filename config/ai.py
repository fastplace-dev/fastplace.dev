"""AI configuration — model routing, embeddings, vector store selection.

API keys are never stored here; LiteLLM reads OPENAI_API_KEY /
ANTHROPIC_API_KEY & friends straight from the environment.
"""

# Any LiteLLM-supported model string ("gpt-4o-mini", "anthropic/claude-…", …)
AI_MODEL = "gpt-4o-mini"
AI_EMBEDDING_MODEL = "text-embedding-3-small"

# Safety bound for the agent tool-call loop (model → tools → model → …).
AI_MAX_TOOL_ROUNDS = 8

# Vector store registry key — "pgvector" (default) or a store registered
# from app/ai/vectors/.
AI_VECTOR_STORE = "pgvector"
