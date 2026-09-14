"""Projects bridge pages — server-driven SPA views (Inertia pattern)."""

from __future__ import annotations

from app.http.requests.create_project_request import CreateProjectRequest
from app.http.requests.create_task_request import CreateTaskRequest
from app.modules.projects.services.projects_service import ProjectsService
from fastplace.errors import NotFoundError
from fastplace.http import Controller, Redirect, Request, render


async def _form_input(request: Request, schema: type) -> dict:
    """Read the payload from a JSON bridge request or a native form POST."""
    if request.is_bridge:
        data = await request.validate(schema)
        return data.model_dump()
    form = await request.form()
    return {key: form.get(key) for key in form.keys()}


def _int_id(request: Request, name: str = "id") -> int:
    """Route ids are strings at the edge — `/projects/abc` is a 404, not a 500."""
    raw = request.param(name)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise NotFoundError(f"{name} {raw!r} not found") from exc


class ProjectsController(Controller):
    service = ProjectsService()

    async def index(self, request: Request):
        projects = await self.service.list_projects()
        return render(request, component="Projects/Index", props={"projects": projects})

    async def show(self, request: Request):
        detail = await self.service.project_detail(_int_id(request))
        return render(request, component="Projects/Show", props={"project": detail})

    async def store(self, request: Request):
        data = await _form_input(request, CreateProjectRequest)
        await self.service.create_project(
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
        )
        return Redirect("/projects", status_code=303)

    async def store_task(self, request: Request):
        project_id = _int_id(request)
        data = await _form_input(request, CreateTaskRequest)
        await self.service.add_task(project_id=project_id, title=str(data.get("title") or ""))
        return Redirect(f"/projects/{project_id}", status_code=303)

    async def toggle_task(self, request: Request):
        task = await self.service.toggle_task(_int_id(request))
        return Redirect(f"/projects/{task['project_id']}", status_code=303)
