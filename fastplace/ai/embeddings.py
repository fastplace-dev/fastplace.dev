"""embed() — provider-aware embeddings routed through LiteLLM (blueprint §9).

The model string is any LiteLLM-supported embedding model; the provider is
inferred from it, and API keys come from the environment (never config
files). Tests swap the seam function instead of monkeypatching litellm.
"""

from __future__ import annotations

from fastplace.config import config


async def _default_embedding_fn(*, model: str, input: list[str]) -> list[list[float]]:
    import litellm

    response = await litellm.aembedding(model=model, input=input)
    return [item["embedding"] for item in response.data]


#: Patch seam for tests — keeps litellm imports lazy and offline-free.
_embedding_fn = _default_embedding_fn


async def embed(text: str, model: str | None = None) -> list[float]:
    """Embed one string into a vector (``AI_EMBEDDING_MODEL`` by default)."""
    vectors = await embed_many([text], model=model)
    return vectors[0]


async def embed_many(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Embed several strings in one call — order preserved."""
    if not texts:
        raise ValueError("embed_many needs at least one text")
    if any(not text for text in texts):
        raise ValueError("embed/embed_many cannot embed an empty text")
    resolved = model or config("AI_EMBEDDING_MODEL", default="text-embedding-3-small")
    return await _embedding_fn(model=resolved, input=list(texts))
