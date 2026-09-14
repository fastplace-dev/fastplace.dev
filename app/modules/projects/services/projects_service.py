"""Projects service — the module's public API for project workflows.

Controllers, jobs, and other modules talk to this service only; never to
the repositories or models underneath. Data leaves the module as typed
Pydantic DTOs — the transport-ready contract from the blueprint: /api/v1
controllers annotate their returns with these models and bridge
controllers pass ``model_dump(mode="json")`` props.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel

from app.modules.projects.models.project import Project
from app.modules.projects.models.task import Task
from app.modules.projects.repositories.project_repository import ProjectRepository
from app.modules.projects.repositories.task_repository import TaskRepository
from fastplace.db import db
from fastplace.errors import NotFoundError, ValidationError

#: Blueprint invariant: a project holds at most 50 open tasks.
MAX_OPEN_TASKS = 50


class TaskResource(BaseModel):
    """Serialization contract for Task records leaving the module."""

    id: int
    project_id: int
    title: str
    completed: bool
    due_date: datetime.date | None = None


class ProjectResource(BaseModel):
    """Serialization contract for Project records leaving the module."""

    id: int
    name: str
    description: str = ""
    task_count: int | None = None
    open_task_count: int | None = None


class ProjectDetail(ProjectResource):
    """One project with its tasks (detail views, both edges)."""

    tasks: list[TaskResource] = []


class ProjectsPage(BaseModel):
    """The `/api/v1/projects` list response."""

    data: list[ProjectResource]
    total: int


class ProjectStats(BaseModel):
    """Aggregates for the dashboard — computed by the database."""

    projects: int
    open_tasks: int
    completed_tasks: int


def project_resource(
    project: Project,
    *,
    task_count: int | None = None,
    open_task_count: int | None = None,
) -> ProjectResource:
    """Build the DTO for a Project record."""
    return ProjectResource(
        id=project.id,
        name=project.name,
        description=project.description,
        task_count=task_count,
        open_task_count=open_task_count,
    )


def task_resource(task: Task) -> TaskResource:
    """Build the DTO for a Task record."""
    return TaskResource(
        id=task.id,
        project_id=task.project_id,
        title=task.title,
        completed=bool(task.completed),
        due_date=task.due_date,
    )


class ProjectsService:
    """Business logic for projects and their tasks."""

    max_open_tasks = MAX_OPEN_TASKS

    def __init__(self) -> None:
        self.projects = ProjectRepository()
        self.tasks = TaskRepository()

    async def list_projects(self, limit: int = 50, offset: int = 0) -> list[ProjectResource]:
        rows = await self.projects.page(limit=limit, offset=offset)
        # One grouped query covers every project on the page — no N+1.
        counts = await self.tasks.counts_for_projects([p.id for p in rows])
        return [
            project_resource(
                p,
                task_count=counts.get(p.id, {}).get("total", 0),
                open_task_count=counts.get(p.id, {}).get("open", 0),
            )
            for p in rows
        ]

    async def create_project(self, *, name: str, description: str = "") -> ProjectResource:
        if not (name or "").strip():
            raise ValidationError("project name is required")
        project = await self.projects.create(name=name.strip(), description=description or "")
        # A fresh project has no tasks — every payload carries integer
        # counts, never nulls (list/store/show agree on the wire contract).
        return project_resource(project, task_count=0, open_task_count=0)

    async def project_detail(self, project_id: int) -> ProjectDetail:
        project = await self.projects.find(project_id)
        if project is None:
            raise NotFoundError(f"project #{project_id} not found")
        tasks = await self.tasks.for_project(project.id)
        detail = ProjectDetail(
            **project_resource(project).model_dump(),
            tasks=[task_resource(t) for t in tasks],
        )
        detail.task_count = len(detail.tasks)
        detail.open_task_count = sum(1 for t in detail.tasks if not t.completed)
        return detail

    async def add_task(
        self,
        *,
        project_id: int,
        title: str,
        due_date: datetime.date | None = None,
    ) -> TaskResource:
        if not (title or "").strip():
            raise ValidationError("task title is required")
        async with db.transaction():
            # Row lock: concurrent add_task calls on one project serialize
            # here (FOR UPDATE on PostgreSQL/MySQL; SQLite's serializing
            # pool), so the count-then-insert below cannot interleave.
            project = await self.projects.find_for_update(project_id)
            if project is None:
                raise NotFoundError(f"project #{project_id} not found")
            open_count = await self.tasks.count_open_for_project(project.id)
            if open_count >= self.max_open_tasks:
                raise ValidationError(
                    f"project #{project_id} reached its limit of {self.max_open_tasks} open tasks"
                )
            task = await self.tasks.create(
                project_id=project.id, title=title.strip(), due_date=due_date
            )
        return task_resource(task)

    async def toggle_task(self, task_id: int) -> TaskResource:
        task = await self.tasks.find(task_id)
        if task is None:
            raise NotFoundError(f"task #{task_id} not found")
        task.completed = not task.completed
        await task.save()
        return task_resource(task)

    async def open_tasks(self, project_id: int) -> list[TaskResource]:
        # The completed-filter runs in the database (repository owns it).
        rows = await self.tasks.open_tasks_for_project(project_id)
        return [task_resource(t) for t in rows]

    async def count_projects(self) -> int:
        """Total projects regardless of page size — API ``total`` fields."""
        return await self.projects.count_all()

    async def stats(self) -> ProjectStats:
        task_split = await self.tasks.counts_all()
        return ProjectStats(
            projects=await self.projects.count_all(),
            open_tasks=task_split["open"],
            completed_tasks=task_split["completed"],
        )
