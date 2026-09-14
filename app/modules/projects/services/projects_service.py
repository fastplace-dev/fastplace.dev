"""Projects service — the module's public API for project workflows.

Controllers, jobs, and other modules talk to this service only; never to
the repositories or models underneath. Data leaves the module as plain
dicts (the transport-ready DTO contract from the blueprint).
"""

from __future__ import annotations

import datetime
from typing import Any

from app.modules.projects.models.project import Project
from app.modules.projects.models.task import Task
from app.modules.projects.repositories.project_repository import ProjectRepository
from app.modules.projects.repositories.task_repository import TaskRepository
from fastplace.db import db
from fastplace.errors import NotFoundError, ValidationError

#: Blueprint invariant: a project holds at most 50 open tasks.
MAX_OPEN_TASKS = 50


def project_resource(
    project: Project,
    *,
    task_count: int | None = None,
    open_task_count: int | None = None,
) -> dict[str, Any]:
    """Serialization contract for Project records leaving the module."""
    data: dict[str, Any] = {
        "id": project.id,
        "name": project.name,
        "description": project.description,
    }
    if task_count is not None:
        data["task_count"] = task_count
    if open_task_count is not None:
        data["open_task_count"] = open_task_count
    return data


def task_resource(task: Task) -> dict[str, Any]:
    """Serialization contract for Task records leaving the module."""
    return {
        "id": task.id,
        "project_id": task.project_id,
        "title": task.title,
        "completed": bool(task.completed),
        "due_date": task.due_date.isoformat() if task.due_date else None,
    }


class ProjectsService:
    """Business logic for projects and their tasks."""

    max_open_tasks = MAX_OPEN_TASKS

    def __init__(self) -> None:
        self.projects = ProjectRepository()
        self.tasks = TaskRepository()

    async def list_projects(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
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

    async def create_project(self, *, name: str, description: str = "") -> dict[str, Any]:
        if not (name or "").strip():
            raise ValidationError("project name is required")
        project = await self.projects.create(name=name.strip(), description=description or "")
        return project_resource(project)

    async def project_detail(self, project_id: int) -> dict[str, Any]:
        project = await self.projects.find(project_id)
        if project is None:
            raise NotFoundError(f"project #{project_id} not found")
        tasks = await self.tasks.for_project(project.id)
        return {**project_resource(project), "tasks": [task_resource(t) for t in tasks]}

    async def add_task(
        self,
        *,
        project_id: int,
        title: str,
        due_date: datetime.date | None = None,
    ) -> dict[str, Any]:
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

    async def toggle_task(self, task_id: int) -> dict[str, Any]:
        task = await self.tasks.find(task_id)
        if task is None:
            raise NotFoundError(f"task #{task_id} not found")
        task.completed = not task.completed
        await task.save()
        return task_resource(task)

    async def open_tasks(self, project_id: int) -> list[dict[str, Any]]:
        # The completed-filter runs in the database (repository owns it).
        rows = await self.tasks.open_tasks_for_project(project_id)
        return [task_resource(t) for t in rows]

    async def count_projects(self) -> int:
        """Total projects regardless of page size — API ``total`` fields."""
        return await self.projects.count_all()

    async def stats(self) -> dict[str, int]:
        """Aggregates for the dashboard — computed by the database."""
        task_split = await self.tasks.counts_all()
        return {
            "projects": await self.projects.count_all(),
            "open_tasks": task_split["open"],
            "completed_tasks": task_split["completed"],
        }
