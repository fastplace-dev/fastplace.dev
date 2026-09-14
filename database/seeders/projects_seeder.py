"""Seed the projects module — `fastplace db:seed` runs this module's run()."""

from __future__ import annotations

_PROJECTS: list[tuple[str, str, list[str]]] = [
    (
        "Framework build",
        "Dogfood the Fastplace framework itself.",
        ["Write the ORM contract", "Ship the React bridge", "Wire the AI assistant"],
    ),
    (
        "Scratchpad",
        "Small experiments and one-off notes.",
        ["Try pgvector locally"],
    ),
]


async def run() -> None:
    from app.modules.projects.models.project import Project
    from app.modules.projects.models.task import Task
    from fastplace.db import db

    # One transaction + idempotency by name: a crash mid-run leaves partial
    # state, and the next run completes only what is missing — never skips
    # (any-row guard) and never duplicates. Model queries carry the default
    # soft-delete scope, so ghosts never satisfy an existence check.
    async with db.transaction():
        known_projects = {p.name: p.id for p in await Project.query().get()}
        known_tasks = {(t.project_id, t.title) for t in await Task.query().get()}
        for name, description, titles in _PROJECTS:
            project_id = known_projects.get(name)
            if project_id is None:
                project_id = (await Project.create(name=name, description=description)).id
            for title in titles:
                if (project_id, title) not in known_tasks:
                    await Task.create(project_id=project_id, title=title)
