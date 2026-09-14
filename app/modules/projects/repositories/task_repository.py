"""Task repository — Task query construction, aggregation pushed to the DB."""

from __future__ import annotations

import datetime

from sqlalchemy import func, select

from app.modules.projects.models.task import Task
from fastplace.orm.session import run_read


class TaskRepository:
    """All Task queries for the projects module (blueprint worked example)."""

    async def for_project(self, project_id: int) -> list[Task]:
        return list(
            await Task.query().where(Task.project_id == project_id).order_by(Task.id.asc()).get()
        )

    async def open_tasks_for_project(self, project_id: int) -> list[Task]:
        """Open tasks only — the filter runs in the database, never in Python."""
        return list(
            await Task.query()
            .where(Task.project_id == project_id)
            .where(Task.completed == False)  # noqa: E712 — SQL boolean, not Python
            .order_by(Task.id.asc())
            .get()
        )

    async def find(self, task_id: int) -> Task | None:
        return await Task.query().find(task_id)

    async def create(
        self,
        *,
        project_id: int,
        title: str,
        due_date: datetime.date | None = None,
    ) -> Task:
        return await Task.create(
            project_id=project_id,
            title=title,
            due_date=due_date,
        )

    async def count_open_for_project(self, project_id: int) -> int:
        # Aggregation runs in the database — never in application memory.
        return int(
            await Task.query()
            .where(Task.project_id == project_id)
            .where(Task.completed == False)  # noqa: E712 — SQL boolean, not Python
            .count()
        )

    async def counts_for_projects(self, project_ids: list[int]) -> dict[int, dict[str, int]]:
        """Total and open task counts per project in ONE grouped query (no N+1)."""
        if not project_ids:
            return {}
        stmt = (
            select(
                Task.project_id,
                func.count(Task.id),
                func.count(Task.id).filter(
                    Task.completed == False  # noqa: E712 — SQL boolean
                ),
            )
            .where(Task.project_id.in_(project_ids))
            # Raw selects bypass the query builder's default soft-delete
            # scope — apply it explicitly so aggregates never see ghost rows.
            .where(Task.deleted_at.is_(None))
            .group_by(Task.project_id)
        )
        result = await run_read(stmt)
        return {
            int(project_id): {"total": int(total), "open": int(open_)}
            for project_id, total, open_ in result.all()
        }

    async def counts_all(self) -> dict[str, int]:
        """Completion split across every task, in two DB aggregates."""
        base = (
            select(Task.completed, func.count(Task.id))
            .where(Task.deleted_at.is_(None))
            .group_by(Task.completed)
        )
        result = await run_read(base)
        split = {bool(completed): int(count) for completed, count in result.all()}
        return {
            "open": split.get(False, 0),
            "completed": split.get(True, 0),
        }
