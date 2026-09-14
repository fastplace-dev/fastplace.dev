"""Dashboard aggregates + knowledge API — cross-module via services only."""

from __future__ import annotations


async def _seed(client):
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


async def test_dashboard_api_aggregates_across_modules(dogfood_client, embedding_seam):
    await _seed(dogfood_client)

    resp = await dogfood_client.get("/api/v1/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["stats"] == {"projects": 2, "open_tasks": 2, "completed_tasks": 1}
    assert body["knowledge_items"] == 1
    assert [p["name"] for p in body["recent_projects"]] == ["Beta", "Alpha"]


async def test_dashboard_bridge_page_gets_the_same_props(dogfood_client, embedding_seam):
    await _seed(dogfood_client)

    resp = await dogfood_client.get("/", headers={"X-Fastplace-Request": "true"})
    body = resp.json()
    assert body["component"] == "Dashboard/Index"
    assert body["props"]["stats"]["projects"] == 2
    assert body["props"]["stats"]["open_tasks"] == 2
    assert len(body["props"]["recent_projects"]) == 2


async def test_knowledge_ingest_via_api(dogfood_client, embedding_seam):
    resp = await dogfood_client.post(
        "/api/v1/knowledge", json={"title": "note", "content": "body text"}
    )
    assert resp.status_code == 201
    assert resp.json()["embedding"] == [0.001] * 1536


async def test_knowledge_search_rejects_runaway_queries(dogfood_client):
    """An unbounded `q` would embed megabytes — capped at the edge (422)."""
    resp = await dogfood_client.get("/api/v1/knowledge/search", params={"q": "x" * 501})
    assert resp.status_code == 422
    assert "q" in resp.json()["errors"]


async def test_knowledge_ingest_requires_content(dogfood_client):
    resp = await dogfood_client.post("/api/v1/knowledge", json={"title": "", "content": ""})
    assert resp.status_code == 422


async def test_knowledge_search_filters_in_the_database(dogfood_client, embedding_seam):
    await dogfood_client.post(
        "/api/v1/knowledge", json={"title": "pgvector", "content": "postgres vectors"}
    )
    await dogfood_client.post(
        "/api/v1/knowledge", json={"title": "vitest", "content": "frontend tests"}
    )

    hits = await dogfood_client.get("/api/v1/knowledge/search", params={"q": "postgres"})
    assert hits.status_code == 200
    assert [h["title"] for h in hits.json()["data"]] == ["pgvector"]
