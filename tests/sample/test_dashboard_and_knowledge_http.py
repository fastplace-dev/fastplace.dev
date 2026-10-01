"""Dashboard aggregates + knowledge API — cross-module via services only."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Drop the process-wide rate-limit cache around each test.

    ThrottleMiddleware counts logins against the shared memory cache, and
    seeding logs in first — without the reset the sixth login in the
    process would 429 regardless of test boundaries.
    """
    from fastplace.cache import reset_cache

    reset_cache()
    yield
    reset_cache()


async def _seed(client):
    await _login_verified_user(client)
    p1 = (await client.post("/api/v1/projects", json={"name": "Alpha"})).json()
    p2 = (await client.post("/api/v1/projects", json={"name": "Beta"})).json()
    await client.post(f"/api/v1/projects/{p1['id']}/tasks", json={"title": "one"})
    task = (await client.post(f"/api/v1/projects/{p1['id']}/tasks", json={"title": "two"})).json()
    await client.patch(f"/api/v1/tasks/{task['id']}/toggle")
    await client.post(f"/api/v1/projects/{p2['id']}/tasks", json={"title": "three"})
    await client.post(
        "/api/v1/knowledge",
        json={"title": "pgvector", "content": "embeddings in postgres"},
    )
    return p1


async def _login_verified_user(client):
    import datetime

    from app.modules.accounts.models.user import User
    from fastplace.auth.hashing import Hash

    if await User.where(User.email == "dash@example.test").first() is None:
        await User.create(
            name="Dash",
            email="dash@example.test",
            password_hash=Hash.make("secret123"),
            email_verified_at=datetime.datetime.now(datetime.UTC),
        )
    response = await client.post(
        "/login", json={"email": "dash@example.test", "password": "secret123"}
    )
    assert response.status_code == 303


async def test_dashboard_api_aggregates_across_modules(sample_client, embedding_seam):
    await _seed(sample_client)

    resp = await sample_client.get("/api/v1/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["stats"] == {"projects": 2, "open_tasks": 2, "completed_tasks": 1}
    assert body["knowledge_items"] == 1
    assert [p["name"] for p in body["recent_projects"]] == ["Beta", "Alpha"]


async def test_dashboard_bridge_page_gets_the_same_props(sample_client, embedding_seam):
    # _seed logs in first — the mutating demo routes are authenticated.
    await _seed(sample_client)

    resp = await sample_client.get("/dashboard", headers={"X-Fastplace-Request": "true"})
    body = resp.json()
    assert body["component"] == "Dashboard/Index"
    assert body["props"]["stats"]["projects"] == 2
    assert body["props"]["stats"]["open_tasks"] == 2
    assert len(body["props"]["recent_projects"]) == 2


async def test_knowledge_ingest_via_api(sample_client, embedding_seam):
    # The knowledge API is authenticated (audit T6) — log in first.
    await _login_verified_user(sample_client)
    resp = await sample_client.post(
        "/api/v1/knowledge", json={"title": "note", "content": "body text"}
    )
    assert resp.status_code == 201
    assert "embedding" not in resp.json()  # the vector never leaves the module


async def test_knowledge_search_rejects_runaway_queries(sample_client):
    """An unbounded `q` would embed megabytes — capped at the edge (422)."""
    await _login_verified_user(sample_client)
    resp = await sample_client.get("/api/v1/knowledge/search", params={"q": "x" * 501})
    assert resp.status_code == 422
    assert "q" in resp.json()["errors"]


async def test_knowledge_ingest_requires_content(sample_client):
    await _login_verified_user(sample_client)
    resp = await sample_client.post("/api/v1/knowledge", json={"title": "", "content": ""})
    assert resp.status_code == 422


async def test_knowledge_search_filters_in_the_database(sample_client, embedding_seam):
    await _login_verified_user(sample_client)
    await sample_client.post(
        "/api/v1/knowledge", json={"title": "pgvector", "content": "postgres vectors"}
    )
    await sample_client.post(
        "/api/v1/knowledge", json={"title": "vitest", "content": "frontend tests"}
    )

    hits = await sample_client.get("/api/v1/knowledge/search", params={"q": "postgres"})
    assert hits.status_code == 200
    assert [h["title"] for h in hits.json()["data"]] == ["pgvector"]
