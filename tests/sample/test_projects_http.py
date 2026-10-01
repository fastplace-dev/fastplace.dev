"""Projects HTTP edge — the same service behind web bridge and JSON API."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Drop the process-wide rate-limit cache around each test.

    ThrottleMiddleware counts logins against the shared memory cache, and
    these tests log in once each — without the reset the sixth login in
    the process would 429 regardless of test boundaries.
    """
    from fastplace.cache import reset_cache

    reset_cache()
    yield
    reset_cache()


async def _login_projects_user(client):
    """A verified user behind a live session cookie — the mutating demo
    routes are authenticated (audit T6), so tests that write log in first."""
    import datetime

    from app.modules.accounts.models.user import User
    from fastplace.auth.hashing import Hash

    if await User.where(User.email == "proj@example.test").first() is None:
        await User.create(
            name="Proj",
            email="proj@example.test",
            password_hash=Hash.make("secret123"),
            email_verified_at=datetime.datetime.now(datetime.UTC),
        )
    response = await client.post(
        "/login", json={"email": "proj@example.test", "password": "secret123"}
    )
    assert response.status_code == 303


# ---------------------------------------------------------------------------
# Unified JSON API (/api/v1)
# ---------------------------------------------------------------------------


async def test_api_lists_projects(sample_client):
    resp = await sample_client.get("/api/v1/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"data": [], "total": 0}


async def test_api_creates_project_with_edge_validation(sample_client):
    await _login_projects_user(sample_client)
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
    await _login_projects_user(sample_client)
    resp = await sample_client.post("/api/v1/projects", json={"name": "  "})
    assert resp.status_code == 422
    assert "errors" in resp.json()


async def test_api_project_detail_with_tasks(sample_client):
    await _login_projects_user(sample_client)
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
    await _login_projects_user(sample_client)
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
    await _login_projects_user(sample_client)
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
    await _login_projects_user(sample_client)
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
    await _login_projects_user(sample_client)
    project = (await sample_client.post("/api/v1/projects", json={"name": "Detail"})).json()
    await sample_client.post(f"/api/v1/projects/{project['id']}/tasks", json={"title": "only task"})

    page = await sample_client.get(
        f"/projects/{project['id']}", headers={"X-Fastplace-Request": "true"}
    )
    body = page.json()
    assert body["component"] == "Projects/Show"
    assert body["props"]["project"]["name"] == "Detail"
    assert [t["title"] for t in body["props"]["project"]["tasks"]] == ["only task"]


async def test_native_form_posts_flow_through_the_request_schema(sample_client):
    """The no-JS form path validates against the same schema as the bridge.

    A native form POST must not bypass the field constraints: oversized
    values redirect back with flashed errors instead of being stored.
    """
    await _login_projects_user(sample_client)

    resp = await sample_client.post(
        "/projects",
        data={"name": "x" * 300, "description": "d" * 2001},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml",
            "Referer": "http://test/projects",
        },
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "http://test/projects"

    listed = await sample_client.get("/api/v1/projects")
    assert listed.json()["total"] == 0  # nothing was persisted


async def test_native_task_form_posts_flow_through_the_request_schema(sample_client):
    await _login_projects_user(sample_client)
    project = (await sample_client.post("/api/v1/projects", json={"name": "Schema"})).json()

    oversize = await sample_client.post(
        f"/projects/{project['id']}/tasks",
        data={"title": "x" * 300},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    bad_date = await sample_client.post(
        f"/projects/{project['id']}/tasks",
        data={"title": "fine title", "due_date": "not-a-date"},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    assert oversize.status_code == 303
    assert bad_date.status_code == 303

    detail = (await sample_client.get(f"/api/v1/projects/{project['id']}")).json()
    assert detail["tasks"] == []


async def test_web_store_redirects_back_to_the_page(sample_client):
    await _login_projects_user(sample_client)
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
    await _login_projects_user(sample_client)
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
    await _login_projects_user(sample_client)
    resp = await sample_client.post(
        "/projects", json={"name": "Bridge POST"}, headers={"X-Fastplace-Request": "true"}
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/projects"

    listed = await sample_client.get("/api/v1/projects")
    assert listed.json()["total"] == 1


# ---------------------------------------------------------------------------
# Auth boundary — the mutating demo routes are authenticated (audit T6)
# ---------------------------------------------------------------------------


async def test_api_mutations_require_authentication(sample_client):
    """Anonymous JSON mutations get the 401 envelope, never the controller."""
    for method, url, payload in (
        ("POST", "/api/v1/projects", {"name": "x"}),
        ("POST", "/api/v1/projects/1/tasks", {"title": "t"}),
        ("PATCH", "/api/v1/tasks/1/toggle", {}),
    ):
        resp = await sample_client.request(method, url, json=payload)
        assert resp.status_code == 401, (method, url, resp.status_code)


async def test_web_mutations_redirect_anonymous_browsers_to_login(sample_client):
    resp = await sample_client.post("/projects", data={"name": "anon"})
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"

    added = await sample_client.post("/projects/1/tasks", data={"title": "anon"})
    assert added.status_code == 302
    assert added.headers["location"] == "/login"

    toggled = await sample_client.post("/tasks/1/toggle")
    assert toggled.status_code == 302
    assert toggled.headers["location"] == "/login"


async def test_authenticated_mutations_pass_the_guard(sample_client):
    await _login_projects_user(sample_client)

    created = await sample_client.post("/api/v1/projects", json={"name": "Authed"})
    assert created.status_code == 201

    task = await sample_client.post(
        f"/api/v1/projects/{created.json()['id']}/tasks", json={"title": "t"}
    )
    assert task.status_code == 201

    toggled = await sample_client.patch(f"/api/v1/tasks/{task.json()['id']}/toggle")
    assert toggled.status_code == 200
    assert toggled.json()["completed"] is True

    web = await sample_client.post("/projects", data={"name": "Web authed"})
    assert web.status_code == 303
