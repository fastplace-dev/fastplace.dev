"""Projects module — models, repository, service (blueprint worked example)."""

from __future__ import annotations

import pytest

from fastplace.errors import NotFoundError, ValidationError


@pytest.fixture()
def service():
    from app.modules.projects.services.projects_service import ProjectsService

    return ProjectsService()


@pytest.fixture()
async def project(service):
    return await service.create_project(name="Framework", description="sample")


async def test_create_project_persists_and_lists(service, sample_db):
    await service.create_project(name="Alpha", description="first")
    await service.create_project(name="Beta")

    projects = await service.list_projects()
    names = [p.name for p in projects]
    assert "Alpha" in names and "Beta" in names
    assert all(p.task_count == 0 for p in projects)


async def test_create_project_rejects_blank_name(service, sample_db):
    with pytest.raises(ValidationError):
        await service.create_project(name="   ")


async def test_task_counts_come_from_the_database(service, sample_db):
    project = await service.create_project(name="With tasks")
    await service.add_task(project_id=project.id, title="write tests")
    await service.add_task(project_id=project.id, title="ship it")
    await service.toggle_task((await service.open_tasks(project.id))[0].id)

    listed = {p.name: p for p in await service.list_projects()}
    assert listed["With tasks"].task_count == 2
    assert listed["With tasks"].open_task_count == 1


async def test_add_task_and_toggle_round_trip(service, sample_db, project):
    task = await service.add_task(project_id=project.id, title="draft ADR")
    assert task.completed is False

    done = await service.toggle_task(task.id)
    assert done.completed is True


async def test_toggle_missing_task_raises_not_found(service, sample_db):
    with pytest.raises(NotFoundError):
        await service.toggle_task(9999)


async def test_open_task_limit_enforced_in_transaction(service, sample_db, monkeypatch):
    # Blueprint invariant: a project holds at most 50 open tasks.
    monkeypatch.setattr(service, "max_open_tasks", 2)
    project = await service.create_project(name="Capped")
    pid = project.id
    await service.add_task(project_id=pid, title="one")
    await service.add_task(project_id=pid, title="two")
    with pytest.raises(ValidationError, match="(?i)limit"):
        await service.add_task(project_id=pid, title="three")


async def test_project_detail_includes_tasks(service, sample_db, project):
    await service.add_task(project_id=project.id, title="a")
    await service.add_task(project_id=project.id, title="b")

    detail = await service.project_detail(project.id)
    assert detail.name == "Framework"
    assert [t.title for t in detail.tasks] == ["a", "b"]


async def test_project_detail_missing_raises_not_found(service, sample_db):
    with pytest.raises(NotFoundError):
        await service.project_detail(4242)


async def test_list_projects_two_queries_no_n_plus_one(service, sample_db):
    # Ten projects with tasks each — the listing must stay at a constant
    # query count (counts aggregated in the database, not per-project).
    from fastplace.orm.instrumentation import activate_tracker, current_stats

    for n in range(10):
        p = await service.create_project(name=f"P{n}")
        await service.add_task(project_id=p.id, title=f"t{n}")

    with activate_tracker():
        await service.list_projects()
        stats = current_stats()
    assert stats is not None
    assert stats.statements <= 3, f"expected constant queries, ran {stats.statements}"


async def test_concurrent_add_task_never_exceeds_the_open_limit(service, sample_db, monkeypatch):
    """The open-task invariant must survive concurrent writers.

    Every gathered coroutine runs as its own asyncio task and opens its own
    connection — a plain check-then-act count lets several transactions pass
    the limit together. The project row must be locked for the transaction.
    """
    import asyncio

    monkeypatch.setattr(service, "max_open_tasks", 5)
    pid = (await service.create_project(name="Concurrent")).id

    results = await asyncio.gather(
        *[service.add_task(project_id=pid, title=f"t{i}") for i in range(12)],
        return_exceptions=True,
    )
    created = [r for r in results if not isinstance(r, BaseException)]
    rejected = [r for r in results if isinstance(r, ValidationError)]
    assert len(created) == 5, f"invariant broken: {len(created)} tasks survived"
    assert len(rejected) == 7
    assert len((await service.project_detail(pid)).tasks) == 5


async def test_open_tasks_filtering_happens_in_the_database(service, sample_db, project):
    from app.modules.projects.repositories.task_repository import TaskRepository

    await service.add_task(project_id=project.id, title="open a")
    second = await service.add_task(project_id=project.id, title="open b")
    third = await service.add_task(project_id=project.id, title="the done one")
    await service.toggle_task(second.id)
    await service.toggle_task(third.id)

    repo = TaskRepository()
    # The repository owns a WHERE completed IS FALSE query ordered by id —
    # the service delegates instead of filtering rows in Python.
    assert [t.title for t in await repo.open_tasks_for_project(project.id)] == ["open a"]
    assert [t.title for t in await service.open_tasks(project.id)] == ["open a"]


async def test_count_projects_counts_beyond_the_page(service, sample_db):
    await service.create_project(name="one")
    await service.create_project(name="two")

    assert len(await service.list_projects(limit=1)) == 1
    assert await service.count_projects() == 2


def test_task_project_id_is_indexed():
    """The tasks.project_id FK is on every project-page query — indexed."""
    from app.modules.projects.models.task import Task

    indexed = {col.name for index in Task.__table__.indexes for col in index.columns}
    assert "project_id" in indexed


async def test_soft_deleted_tasks_leave_the_aggregates(service, sample_db, project):
    """Every model carries a soft-delete ``deleted_at``; the raw-select
    aggregates must respect it as strictly as the query builder does."""
    from app.modules.projects.models.task import Task

    await service.add_task(project_id=project.id, title="kept")
    gone = await service.add_task(project_id=project.id, title="gone")
    doomed = await Task.query().find(gone.id)
    await doomed.delete()

    listed = {p.name: p for p in await service.list_projects()}
    assert listed["Framework"].task_count == 1
    assert listed["Framework"].open_task_count == 1

    split = await service.stats()
    assert split.open_tasks == 1
    assert split.completed_tasks == 0


def test_long_text_columns_use_the_text_type():
    """Unbounded text (2000-char schema caps) must not sit in VARCHAR(255)."""
    from sqlalchemy import Text

    from app.modules.knowledge.models.knowledge_item import KnowledgeItem
    from app.modules.projects.models.project import Project

    assert isinstance(Project.__table__.columns["description"].type, Text)
    assert isinstance(KnowledgeItem.__table__.columns["content"].type, Text)
