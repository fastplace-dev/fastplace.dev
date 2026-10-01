"""Knowledge HTTP edge — the T6 auth/throttle boundary and the vector leak.

Ingest and search both spend a real embedding-provider call on vector
backends, so the API surface carries the same guardrails as the assistant
route: authenticated and throttled. The stored embedding vector itself is
module-internal — it never rides a public response.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Drop the process-wide rate-limit cache around each test."""
    from fastplace.cache import reset_cache

    reset_cache()
    yield
    reset_cache()


async def _login_knowledge_user(client):
    import datetime

    from app.modules.accounts.models.user import User
    from fastplace.auth.hashing import Hash

    if await User.where(User.email == "know@example.test").first() is None:
        await User.create(
            name="Know",
            email="know@example.test",
            password_hash=Hash.make("secret123"),
            email_verified_at=datetime.datetime.now(datetime.UTC),
        )
    response = await client.post(
        "/login", json={"email": "know@example.test", "password": "secret123"}
    )
    assert response.status_code == 303


async def test_knowledge_routes_require_authentication(sample_client):
    """Anonymous traffic never reaches the provider-backed service (audit T6)."""
    resp = await sample_client.post("/api/v1/knowledge", json={"title": "t", "content": "c"})
    assert resp.status_code == 401

    search = await sample_client.get("/api/v1/knowledge/search", params={"q": "anything"})
    assert search.status_code == 401


async def test_knowledge_responses_do_not_carry_the_embedding_vector(sample_client, embedding_seam):
    """The stored vector is module-internal — store and search responses
    expose the text, never the embedding."""
    await _login_knowledge_user(sample_client)

    created = await sample_client.post(
        "/api/v1/knowledge", json={"title": "note", "content": "body text"}
    )
    assert created.status_code == 201
    assert "embedding" not in created.json()

    hits = await sample_client.get("/api/v1/knowledge/search", params={"q": "body"})
    assert hits.status_code == 200
    assert hits.json()["data"], "the ingested item should match"
    for hit in hits.json()["data"]:
        assert "embedding" not in hit


async def test_knowledge_store_is_throttled(sample_client, embedding_seam):
    """Runaway ingest loops get the same brake the assistant route has."""
    await _login_knowledge_user(sample_client)

    throttled = None
    for _ in range(11):
        throttled = await sample_client.post(
            "/api/v1/knowledge", json={"title": "spam", "content": "x"}
        )
    assert throttled.status_code == 429
    assert int(throttled.headers["Retry-After"]) >= 1


async def test_knowledge_search_is_throttled(sample_client, embedding_seam):
    await _login_knowledge_user(sample_client)

    throttled = None
    for _ in range(11):
        throttled = await sample_client.get("/api/v1/knowledge/search", params={"q": "x"})
    assert throttled.status_code == 429
