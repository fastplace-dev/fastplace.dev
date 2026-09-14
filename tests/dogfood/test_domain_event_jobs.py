"""Dogfood domain events — Project lifecycle → knowledge ingestion job.

The blueprint's cross-module rule (§3 boundary rule 4): creating a project
must not call the knowledge module. The projects model dispatches
``project_created``; a job registered under the same name (app/jobs/) consumes
it and ingests the project into the knowledge base.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolated_queue():
    from fastplace.events import reset_listeners
    from fastplace.queue import reset_queue, reset_registry

    reset_listeners()
    reset_registry()
    reset_queue()
    yield
    reset_listeners()
    reset_registry()
    reset_queue()


def test_app_jobs_register_the_ingestion_handler():
    from fastplace.queue import import_jobs, registered_jobs

    names = import_jobs(_PROJECT_ROOT)
    assert "project_created" in names
    assert "project_created" in registered_jobs()


async def test_project_create_dispatches_and_the_job_ingests(dogfood_db, embedding_seam):
    from fastplace.queue import MemoryQueue, import_jobs, queue

    import_jobs(_PROJECT_ROOT)

    from app.modules.projects.services.projects_service import ProjectsService

    created = await ProjectsService().create_project(name="Event Bridge", description="morphs")

    memory = queue()
    assert isinstance(memory, MemoryQueue)
    assert len(memory.pending) == 1
    assert memory.pending[0].name == "project_created"
    assert memory.pending[0].kwargs == {"model": "Project", "id": created.id}

    # Draining the queue does the cross-module work — the knowledge module
    # learns about the project without the projects module ever importing it.
    executed = await memory.run_pending()
    assert executed == 1
    assert not memory.failures

    from app.modules.knowledge.services.knowledge_service import KnowledgeService

    hits = await KnowledgeService().search("Event Bridge")
    assert any("Event Bridge" in item.title for item in hits)


async def test_knowledge_side_effect_needs_no_listener(dogfood_db):
    """The in-process listener registry stays empty — the only consumer is
    the queued job, exactly the fire-and-forget shape rule 4 asks for."""
    from fastplace.events import _listeners
    from fastplace.queue import import_jobs

    import_jobs(_PROJECT_ROOT)
    assert "project_created" not in _listeners
