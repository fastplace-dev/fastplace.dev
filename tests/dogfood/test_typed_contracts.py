"""Typed DTO contracts — module services ship Pydantic result models.

The blueprint's `-> DashboardOverview` pattern: services own result models,
/api/v1 controllers annotate their returns with them (the framework's
outbound validation is the contract), and bridge controllers pass
`model_dump(mode="json")` props.
"""

from __future__ import annotations

import datetime

import pytest
from pydantic import BaseModel


@pytest.fixture()
async def db_and_service(dogfood_db):
    from app.modules.projects.services.projects_service import ProjectsService

    return ProjectsService()


async def test_projects_service_returns_typed_resources(db_and_service):
    from app.modules.projects.services.projects_service import (
        ProjectDetail,
        ProjectResource,
        TaskResource,
    )

    service = db_and_service
    project = await service.create_project(name="DTO", description="typed contract")
    assert isinstance(project, ProjectResource)

    task = await service.add_task(
        project_id=project.id,
        title="annotate returns",
        due_date=datetime.date(2026, 10, 1),
    )
    assert isinstance(task, TaskResource)

    detail = await service.project_detail(project.id)
    assert isinstance(detail, ProjectDetail)
    assert [t.title for t in detail.tasks] == ["annotate returns"]


async def test_resource_dump_is_json_mode(db_and_service):
    """model_dump(mode='json') — dates become strings, exactly what the
    bridge props and the JSON API must carry."""
    service = db_and_service
    project = await service.create_project(name="Dates")
    task = await service.add_task(
        project_id=project.id, title="due", due_date=datetime.date(2026, 12, 24)
    )

    dumped = task.model_dump(mode="json")
    assert dumped["due_date"] == "2026-12-24"
    assert dumped["completed"] is False


async def test_api_controllers_annotate_response_schemas():
    """The unified API's contract is declared on the controller returns."""
    from app.http.controllers.dashboard_api_controller import DashboardApiController
    from app.http.controllers.knowledge_api_controller import KnowledgeApiController
    from app.http.controllers.projects_api_controller import ProjectsApiController
    from fastplace.http.serialization import _cached_type_hints

    for handler in (
        DashboardApiController.index,
        ProjectsApiController.index,
        ProjectsApiController.show,
        KnowledgeApiController.search,
    ):
        annotation = _cached_type_hints(handler).get("return")
        assert isinstance(annotation, type) and issubclass(annotation, BaseModel), (
            f"{handler.__qualname__} must annotate a Pydantic return schema"
        )


async def test_dashboard_service_composes_a_typed_overview(dogfood_db, embedding_seam):
    from app.modules.dashboard.services.dashboard_service import (
        DashboardOverview,
        DashboardService,
    )

    overview = await DashboardService().compose(url="/")
    assert isinstance(overview, DashboardOverview)
    assert overview.appName == "Fastplace"
    assert overview.url == "/"
    assert overview.stats.projects == 0

    dumped = overview.model_dump(mode="json")
    assert dumped["stats"] == {"projects": 0, "open_tasks": 0, "completed_tasks": 0}
    assert dumped["recent_projects"] == []
    assert dumped["knowledge_items"] == 0


async def test_knowledge_service_returns_typed_items(dogfood_db, embedding_seam):
    from app.modules.knowledge.services.knowledge_service import (
        KnowledgeItemResource,
        KnowledgeService,
    )

    service = KnowledgeService()
    item = await service.ingest(title="typed", content="knowledge dto")
    assert isinstance(item, KnowledgeItemResource)

    hits = await service.search("typed")
    assert all(isinstance(hit, KnowledgeItemResource) for hit in hits)


async def test_api_and_bridge_payloads_keep_the_documented_shape(dogfood_client):
    """Typing must not move keys — the wire contract is frozen."""
    created = await dogfood_client.post("/api/v1/projects", json={"name": "Shape"})
    assert created.status_code == 201

    api_resp = await dogfood_client.get("/api/v1/projects")
    assert api_resp.status_code == 200
    body = api_resp.json()
    assert set(body) == {"data", "total"}
    assert body["total"] == 1
    assert set(body["data"][0]) == {"id", "name", "description", "task_count", "open_task_count"}

    bridge_resp = await dogfood_client.get("/projects", headers={"X-Fastplace-Request": "true"})
    props = bridge_resp.json()["props"]
    assert set(props["projects"][0]) == {
        "id",
        "name",
        "description",
        "task_count",
        "open_task_count",
    }


async def test_store_and_show_payloads_carry_real_counts_not_nulls(dogfood_client):
    """Every project payload carries integer counts — list, store, and show
    agree on the same keys with the same types (no null-when-unknown drift)."""
    created = (await dogfood_client.post("/api/v1/projects", json={"name": "Counted"})).json()
    assert created["task_count"] == 0
    assert created["open_task_count"] == 0

    await dogfood_client.post(f"/api/v1/projects/{created['id']}/tasks", json={"title": "a"})
    second = (
        await dogfood_client.post(f"/api/v1/projects/{created['id']}/tasks", json={"title": "b"})
    ).json()
    await dogfood_client.patch(f"/api/v1/tasks/{second['id']}/toggle")

    shown = (await dogfood_client.get(f"/api/v1/projects/{created['id']}")).json()
    assert shown["task_count"] == 2
    assert shown["open_task_count"] == 1

    bridge_shown = await dogfood_client.get(
        f"/projects/{created['id']}", headers={"X-Fastplace-Request": "true"}
    )
    project = bridge_shown.json()["props"]["project"]
    assert project["task_count"] == 2
    assert project["open_task_count"] == 1
