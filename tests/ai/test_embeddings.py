"""embed() — provider-aware embeddings routed through LiteLLM (T5.4)."""

import pytest

import fastplace.ai.embeddings as embeddings


@pytest.fixture(autouse=True)
def _clean_seam():
    embeddings._embedding_fn = embeddings._default_embedding_fn
    yield
    embeddings._embedding_fn = embeddings._default_embedding_fn


def _fake_embedding_fn(calls):
    async def fn(*, model, input):
        calls.append({"model": model, "input": input})
        return [[0.1, 0.2, 0.3] for _ in input]

    return fn


async def test_embed_returns_the_first_vector():
    calls = []
    embeddings._embedding_fn = _fake_embedding_fn(calls)

    vector = await embeddings.embed("hello world")

    assert vector == [0.1, 0.2, 0.3]
    assert calls == [{"model": "text-embedding-3-small", "input": ["hello world"]}]


async def test_embed_honors_an_explicit_model():
    calls = []
    embeddings._embedding_fn = _fake_embedding_fn(calls)

    await embeddings.embed("hello", model="voyage-3")

    assert calls[0]["model"] == "voyage-3"


async def test_embed_many_keeps_input_order():
    calls = []
    embeddings._embedding_fn = _fake_embedding_fn(calls)

    vectors = await embeddings.embed_many(["a", "b", "c"])

    assert vectors == [[0.1, 0.2, 0.3]] * 3
    assert calls[0]["input"] == ["a", "b", "c"]


async def test_embed_model_defaults_from_config(monkeypatch):
    monkeypatch.setenv("AI_EMBEDDING_MODEL", "custom-embed")
    calls = []
    embeddings._embedding_fn = _fake_embedding_fn(calls)

    await embeddings.embed("x")

    assert calls[0]["model"] == "custom-embed"


async def test_embed_rejects_empty_input():
    with pytest.raises(ValueError, match="text"):
        await embeddings.embed("")
    with pytest.raises(ValueError, match="text"):
        await embeddings.embed_many([])
    with pytest.raises(ValueError, match="empty"):
        await embeddings.embed_many(["ok", ""])
