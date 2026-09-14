"""Projects HTTP edge — the same service behind web bridge and JSON API."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Unified JSON API (/api/v1)
# ---------------------------------------------------------------------------


async def test_api_lists_projects(sample_client):
    resp = await sample_client.get("/api/v1/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"data": [], "total": 0}


async def test_api_creates_project_with_edge_validation(sample_client):
    created = await sample_client.post(
        "/api/v1/projects", json={"name": "Framework", "description": "sample"}
    )
    assert created.status_code == 201
    assert created.json()["name"] == "Framework"

    listed = await sample_client.get("/api/v1/projects")
    assert listed.json()["total"] == 1
    assert listed.json()["data"][0]["name"] == "Framework"
    assert listed.json()["data"][0]["task_count"] == 0


async def test_api_rejects_blank_name_at_the_edge(sample_client):
    resp = await sample_client.post("/api/v1/projects", json={"name": "  "})
    assert resp.status_code == 422
    assert "errors" in resp.json()


async def test_api_project_detail_with_tasks(sample_client):
    created = (await sample_client.post("/api/v1/projects", json={"name": "P"})).json()
    task = await sample_client.post(
        f"/api/v1/projects/{created['id']}/tasks", json={"title": "write ADR"}
    )
    assert task.status_code == 201
    assert task.json()["completed"] is False

    detail = await sample_client.get(f"/api/v1/projects/{created['id']}")
    assert detail.status_code == 200
    assert [t["title"] for t in detail.json()["tasks"]] == ["write ADR"]


async def test_api_missing_project_is_404_json(sample_client):
    resp = await sample_client.get("/api/v1/projects/9999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["message"]


async def test_api_non_numeric_route_ids_are_404_not_500(sample_client):
    """`/projects/abc` must resolve to a 404, never an int() ValueError."""
    for method, url in (
        ("GET", "/api/v1/projects/abc"),
        ("POST", "/api/v1/projects/abc/tasks"),
        ("PATCH", "/api/v1/tasks/abc/toggle"),
    ):
        resp = await sample_client.request(method, url, json={"title": "x"})
        assert resp.status_code == 404, (method, url, resp.status_code)


async def test_web_non_numeric_route_ids_are_404_not_500(sample_client):
    resp = await sample_client.get("/projects/not-a-number")
    assert resp.status_code == 404


async def test_api_toggle_task_flips_completion(sample_client):
    project = (await sample_client.post("/api/v1/projects", json={"name": "P"})).json()
    task = (
        await sample_client.post(f"/api/v1/projects/{project['id']}/tasks", json={"title": "t"})
    ).json()

    toggled = await sample_client.patch(f"/api/v1/tasks/{task['id']}/toggle")
    assert toggled.status_code == 200
    assert toggled.json()["completed"] is True


# ---------------------------------------------------------------------------
# SPA bridge pages (/)
# ---------------------------------------------------------------------------


async def test_bridge_renders_projects_page(sample_client):
    await sample_client.post("/api/v1/projects", json={"name": "Bridge project"})

    page = await sample_client.get("/projects", headers={"X-Fastplace-Request": "true"})
    assert page.status_code == 200
    body = page.json()
    assert body["component"] == "Projects/Index"
    assert body["props"]["projects"][0]["name"] == "Bridge project"


async def test_bridge_initial_load_is_html(sample_client):
    page = await sample_client.get("/projects")
    assert page.headers["content-type"].startswith("text/html")
    assert "Projects/Index" in page.text


async def test_bridge_project_show_page(sample_client):
    project = (await sample_client.post("/api/v1/projects", json={"name": "Detail"})).json()
    await sample_client.post(f"/api/v1/projects/{project['id']}/tasks", json={"title": "only task"})

    page = await sample_client.get(
        f"/projects/{project['id']}", headers={"X-Fastplace-Request": "true"}
    )
    body = page.json()
    assert body["component"] == "Projects/Show"
    assert body["props"]["project"]["name"] == "Detail"
    assert [t["title"] for t in body["props"]["project"]["tasks"]] == ["only task"]


async def test_web_store_redirects_back_to_the_page(sample_client):
    resp = await sample_client.post(
        "/projects",
        data={"name": "Form project", "description": "via form"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/projects"

    listed = await sample_client.get("/api/v1/projects")
    assert listed.json()["total"] == 1


async def test_web_add_task_and_toggle_redirect_back(sample_client):
    project = (await sample_client.post("/api/v1/projects", json={"name": "Web"})).json()

    added = await sample_client.post(
        f"/projects/{project['id']}/tasks",
        data={"title": "from the form"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert added.status_code == 303
    assert added.headers["location"] == f"/projects/{project['id']}"

    detail = (await sample_client.get(f"/api/v1/projects/{project['id']}")).json()
    task = detail["tasks"][0]

    toggled = await sample_client.post(f"/tasks/{task['id']}/toggle")
    assert toggled.status_code == 303
    assert toggled.headers["location"] == f"/projects/{project['id']}"

    detail = (await sample_client.get(f"/api/v1/projects/{project['id']}")).json()
    assert detail["tasks"][0]["completed"] is True


async def test_bridge_json_post_creates_project(sample_client):
    resp = await sample_client.post(
        "/projects", json={"name": "Bridge POST"}, headers={"X-Fastplace-Request": "true"}
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/projects"

    listed = await sample_client.get("/api/v1/projects")
    assert listed.json()["total"] == 1
