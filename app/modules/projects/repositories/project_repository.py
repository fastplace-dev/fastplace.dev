"""Project repository — all Project query construction lives here."""

from __future__ import annotations

from sqlalchemy import select

from app.modules.projects.models.project import Project
from fastplace.orm.session import run_read


class ProjectRepository:
    """Data access for projects; returns model instances, never HTTP shapes."""

    async def page(self, limit: int = 50, offset: int = 0) -> list[Project]:
        return list(
            await Project.query()
            .order_by(Project.created_at.desc(), Project.id.desc())
            .limit(limit)
            .offset(offset)
            .get()
        )

    async def find(self, project_id: int) -> Project | None:
        return await Project.query().find(project_id)

    async def find_for_update(self, project_id: int) -> Project | None:
        """Lock the project row for the caller's transaction.

        ``SELECT ... FOR UPDATE`` serializes concurrent writers on the same
        project (PostgreSQL/MySQL); on SQLite it is a no-op — the engine's
        serializing connection pool provides the exclusion there. Joins the
        ambient ``db.transaction()`` session via ``run_read``.
        """
        stmt = select(Project).where(Project.id == project_id).with_for_update()
        result = await run_read(stmt)
        return result.scalars().first()

    async def create(self, *, name: str, description: str) -> Project:
        return await Project.create(name=name, description=description)

    async def count_all(self) -> int:
        return await Project.query().count()
