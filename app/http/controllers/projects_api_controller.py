"""Projects JSON API — thin: validate, delegate to the service, respond."""

from __future__ import annotations

from app.http.requests.create_project_request import CreateProjectRequest
from app.http.requests.create_task_request import CreateTaskRequest
from app.modules.projects.services.projects_service import ProjectsService
from fastplace.errors import NotFoundError
from fastplace.http import Controller, Json, Request


def _int_id(request: Request, name: str = "id") -> int:
    """Route ids are strings at the edge — `/projects/abc` is a 404, not a 500."""
    raw = request.param(name)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise NotFoundError(f"{name} {raw!r} not found") from exc


class ProjectsApiController(Controller):
    service = ProjectsService()

    async def index(self, request: Request):
        # `total` reflects the whole table — the page itself may be truncated.
        return {
            "data": await self.service.list_projects(),
            "total": await self.service.count_projects(),
        }

    async def store(self, request: Request):
        data = await request.validate(CreateProjectRequest)
        project = await self.service.create_project(name=data.name, description=data.description)
        return Json(project, status_code=201)

    async def show(self, request: Request):
        detail = await self.service.project_detail(_int_id(request))
        return Json(detail)

    async def store_task(self, request: Request):
        data = await request.validate(CreateTaskRequest)
        task = await self.service.add_task(
            project_id=_int_id(request),
            title=data.title,
            due_date=data.due_date,
        )
        return Json(task, status_code=201)

    async def toggle_task(self, request: Request):
        task = await self.service.toggle_task(_int_id(request))
        return Json(task)
