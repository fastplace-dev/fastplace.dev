"""Seeders — `fastplace db:seed` populates the sample-app modules idempotently."""

from __future__ import annotations

import asyncio
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parents[2])


def _fresh_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/seed.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    # No provider key in tests — knowledge seeds without embeddings.
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_seeders_populate_and_rerun_is_idempotent(monkeypatch, tmp_path):
    from fastplace.db import reset_db

    _fresh_env(monkeypatch, tmp_path)

    async def _create_schema():
        from app.modules.knowledge.models.knowledge_item import KnowledgeItem  # noqa: F401
        from app.modules.projects.models.project import Project  # noqa: F401
        from app.modules.projects.models.task import Task  # noqa: F401
        from fastplace.db import db

        await db.create_all()

    reset_db()
    asyncio.run(_create_schema())

    from fastplace.orm.migrations.manager import run_seeders

    ran_first = run_seeders(PROJECT_ROOT)
    assert any("projects" in name for name in ran_first)
    assert any("knowledge" in name for name in ran_first)

    # Second run completes without duplicating anything (fresh loop/engine).
    reset_db()
    asyncio.run(_create_schema())
    ran_second = run_seeders(PROJECT_ROOT)
    assert len(ran_second) == len(ran_first)

    async def _counts():
        from app.modules.knowledge.models.knowledge_item import KnowledgeItem
        from app.modules.projects.models.project import Project
        from app.modules.projects.models.task import Task

        return (
            await Project.query().count(),
            await Task.query().count(),
            await KnowledgeItem.query().count(),
        )

    reset_db()
    asyncio.run(_create_schema())
    projects, tasks, items = asyncio.run(_counts())
    assert projects == 2
    assert tasks >= 2
    assert items == 2


def test_seeder_completes_partial_state_without_duplicating(monkeypatch, tmp_path):
    """A crash mid-seed must not wedge the seeder: the next run completes
    what is missing instead of skipping (any-row guard) or duplicating."""
    from fastplace.db import reset_db

    _fresh_env(monkeypatch, tmp_path)

    async def _prepare():
        from app.modules.knowledge.models.knowledge_item import KnowledgeItem  # noqa: F401
        from app.modules.projects.models.project import Project  # noqa: F401
        from app.modules.projects.models.task import Task  # noqa: F401
        from app.modules.projects.services.projects_service import ProjectsService
        from fastplace.db import db

        await db.create_all()
        # Simulate the crash window: the first project landed, its tasks did not.
        await ProjectsService().create_project(
            name="Framework build",
            description="Sample app for the Fastplace framework itself.",
        )

    reset_db()
    asyncio.run(_prepare())

    from fastplace.orm.migrations.manager import run_seeders

    run_seeders(PROJECT_ROOT)

    async def _assert():
        from app.modules.projects.services.projects_service import ProjectsService

        service = ProjectsService()
        projects = {p.name: p for p in await service.list_projects()}
        assert set(projects) == {"Framework build", "Scratchpad"}  # no duplicate
        detail = await service.project_detail(projects["Framework build"].id)
        assert [t.title for t in detail.tasks] == [
            "Write the ORM contract",
            "Ship the React bridge",
            "Wire the AI assistant",
        ]

    async def _create_schema():
        from app.modules.knowledge.models.knowledge_item import KnowledgeItem  # noqa: F401
        from app.modules.projects.models.project import Project  # noqa: F401
        from app.modules.projects.models.task import Task  # noqa: F401
        from fastplace.db import db

        await db.create_all()

    reset_db()
    asyncio.run(_create_schema())
    asyncio.run(_assert())


def test_seeder_reseeds_after_a_project_is_soft_deleted(monkeypatch, tmp_path):
    """A soft-deleted row is invisible to the seeder's existence checks —
    re-seeding rebuilds the missing project with its tasks (no ghosts matched)."""
    from fastplace.db import reset_db

    _fresh_env(monkeypatch, tmp_path)

    async def _create_schema():
        from app.modules.knowledge.models.knowledge_item import KnowledgeItem  # noqa: F401
        from app.modules.projects.models.project import Project  # noqa: F401
        from app.modules.projects.models.task import Task  # noqa: F401
        from fastplace.db import db

        await db.create_all()

    from fastplace.orm.migrations.manager import run_seeders

    reset_db()
    asyncio.run(_create_schema())
    run_seeders(PROJECT_ROOT)  # baseline: both projects exist

    async def _soft_delete_framework():
        from sqlalchemy import select

        from app.modules.projects.models.project import Project
        from fastplace.orm.session import run_read

        row = (
            (await run_read(select(Project).where(Project.name == "Framework build")))
            .scalars()
            .one()
        )
        await row.delete()

    reset_db()
    asyncio.run(_create_schema())
    asyncio.run(_soft_delete_framework())
    run_seeders(PROJECT_ROOT)

    async def _assert():
        from sqlalchemy import select

        from app.modules.projects.models.project import Project
        from fastplace.orm.session import run_read

        live = (await run_read(select(Project).where(Project.deleted_at.is_(None)))).scalars().all()
        names = sorted(p.name for p in live)
        assert names == ["Framework build", "Scratchpad"]  # rebuilt, not matched to the ghost

    reset_db()
    asyncio.run(_create_schema())
    asyncio.run(_assert())
