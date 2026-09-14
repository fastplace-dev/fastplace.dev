"""Cross-module ingestion jobs.

The projects module never imports the knowledge module — it dispatches the
``project_created`` domain event (``Project.__dispatches__``) and this job,
registered under the same name, consumes it on the queue. Payload keys follow
the framework's domain-event bridge: ``model`` (class name) and ``id`` (pk).
"""

from __future__ import annotations

from fastplace.queue import Job


@Job()
async def project_created(model: str, id: int) -> None:
    """Index a newly created project into the knowledge base.

    Jobs hold the same place in the architecture as controllers and tools:
    they call module services, never repositories or models directly.
    """
    from app.modules.knowledge.services.knowledge_service import KnowledgeService
    from app.modules.projects.services.projects_service import ProjectsService

    detail = await ProjectsService().project_detail(id)
    await KnowledgeService().ingest(
        title=f"Project: {detail.name}",
        content=detail.description or f"New project '{detail.name}' created.",
        embed_vector=False,  # project blurbs are short — no vector to keep
    )
