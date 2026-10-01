"""Code generators — make:model, make:controller, make:service, make:page…"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import typer
from rich.panel import Panel
from rich.text import Text

from fastplace.console import console

generators_app = typer.Typer(help="Generate framework scaffolding.")


def _project_root() -> Path:
    return Path.cwd()


def _snake(name: str) -> str:
    """PascalCase → snake_case (``HealthCheck`` → ``health_check``)."""
    out: list[str] = []
    for i, ch in enumerate(name):
        if (
            ch.isupper()
            and i > 0
            and (not name[i - 1].isupper() or (i + 1 < len(name) and name[i + 1].islower()))
        ):
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _page_component_name(stem: str) -> str:
    """Build a valid JS identifier from a page path (``my-settings`` → ``MySettings``)."""
    parts = [seg[:1].upper() + seg[1:] for seg in re.split(r"[/_-]+", stem) if seg]
    return "".join(parts)


def _plural(word: str) -> str:
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


def _pascal(module: str) -> str:
    """snake_case module → the PascalCase entity it holds (``order`` → ``Order``)."""
    return "".join(part[:1].upper() + part[1:] for part in module.split("_") if part)


def _clean_name(name: str, what: str) -> str:
    """Validate a maker NAME into its snake_case form (rejects path shapes)."""
    clean = _snake(name.strip().strip("/"))
    if not clean or not re.fullmatch(r"[a-z][a-z0-9_]*", clean):
        console.print(f"[red]invalid {what} name[/] — use letters/digits starting with a letter")
        raise typer.Exit(code=1)
    return clean


def _clean_module(module: str) -> str:
    """Validate a ``--module`` (or a make:model's derived module segment).

    The value is joined into ``root / "app" / "modules" / module / ...`` and
    written, so anything that escapes ``app/modules`` — an absolute path, a
    ``..`` segment, an empty segment — is an arbitrary-write primitive, not a
    module name. Normal single- (``blog``) and multi-segment names
    (``shop/billing``, ``shop.billing``) pass through unchanged.
    """
    candidate = module.strip()
    segments = candidate.split("/") if candidate else []
    if (
        not candidate
        or candidate.startswith("/")
        or "\\" in candidate
        or PureWindowsPath(candidate).is_absolute()
        or any(segment in ("", ".", "..") for segment in segments)
    ):
        console.print(
            "[red]invalid module name[/] — must stay inside app/modules "
            "(no absolute paths, '..', or empty segments)"
        )
        raise typer.Exit(code=1)
    return candidate


def _write(path: Path, content: str, root: Path, *, force: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        console.print(f"[yellow]exists[/] {path.relative_to(root)}")
        return
    verb = "overwritten" if path.exists() else "created"
    path.write_text(content)
    console.print(f"[green]{verb}[/] {path.relative_to(root)}")


_MODEL_TEMPLATE = '''"""{doc_name} model."""

from fastplace.orm import Field, Model


class {name}(Model):
    __tablename__ = "{table}"

    id: int = Field(primary_key=True)
    name: str
'''


@generators_app.command("make:model")
def make_model(
    name: str = typer.Argument(..., help="Model name in PascalCase, e.g. Project"),
    migration: bool = typer.Option(
        False, "--migration", "-m", help="Also autogenerate a migration."
    ),
    controller: bool = typer.Option(
        False, "--controller", "-c", help="Also create a controller (Product → ProductController)."
    ),
    service: bool = typer.Option(
        False, "--service", "-s", help="Also create a service in the model's module."
    ),
    repository: bool = typer.Option(
        False, "--repository", "-r", help="Also create a repository in the model's module."
    ),
    all: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Also create the controller, service and repository (migration stays under -m).",
    ),
) -> None:
    """Create an ORM model in app/modules/<module>/models/ (plus companions)."""
    from fastplace.orm.fields import resolve_annotation  # noqa: F401 — import sanity

    root = _project_root()
    # _snake matches make:module's entity/module split — BlogPost lands in
    # blog_post/ with table blog_posts, not a mixed-case blogPost module.
    module = _clean_module(_snake(name))
    model_path = root / "app" / "modules" / module / "models" / f"{module}.py"

    _write(
        model_path,
        _MODEL_TEMPLATE.format(doc_name=name, name=name, table=_plural(module)),
        root,
    )
    # Package markers so pkgutil discovery (import_all_models) finds it.
    for marker in (
        root / "app" / "__init__.py",
        root / "app" / "modules" / "__init__.py",
        root / "app" / "modules" / module / "__init__.py",
        root / "app" / "modules" / module / "models" / "__init__.py",
    ):
        if not marker.exists():
            _write(marker, "", root)

    # Companions reuse the sibling makers directly — same templates, same
    # no-clobber rules, one convention (spec E3).
    if controller or all:
        make_controller(name=name if name.endswith("Controller") else f"{name}Controller")
    if service or all:
        make_service(name=name, module=module)
    if repository or all:
        make_repository(name=name, module=module)

    if migration:
        _make_migration(root, module)
    # Same rationale as make:module's summary — the derived module and table
    # are otherwise only visible inside the generated file.
    console.print(f"\n[bold]model {name}[/bold] — module {module}, table {_plural(module)}")


def _make_migration(root: Path, module: str) -> None:
    """Autogenerate a create-<plural>-table migration (shared by make:model / make:module)."""
    from fastplace.cli.database import _manager

    manager = _manager()
    if not manager.configured:
        manager.scaffold()  # works on a fresh project
    revision = manager.make(f"create_{_plural(module)}_table")
    if revision is not None:
        console.print(f"[green]created[/] {revision.relative_to(root)}")


_CONTROLLER_TEMPLATE = '''"""{doc_name} controller."""

from fastplace.http import Controller, Json, Request


class {name}(Controller):
    async def index(self, request: Request):
        return Json({{"items": []}})
'''

# Resource actions in canonical order, each with its Json placeholder payload
# (spec E2). The API variant drops the create/edit form actions.
_RESOURCE_ACTIONS: tuple[tuple[str, str], ...] = (
    ("index", '{"items": []}'),
    ("create", '{"form": "create"}'),
    ("store", '{"created": True}'),
    ("show", '{"item": None}'),
    ("edit", '{"form": "edit"}'),
    ("update", '{"updated": True}'),
    ("destroy", '{"deleted": True}'),
)
_FORM_ACTIONS = frozenset({"create", "edit"})

_RESOURCE_CONTROLLER_TEMPLATE = '''"""{doc_name} controller."""

from fastplace.http import Controller, Json, Request


class {name}(Controller):
{methods}
'''


def _resource_controller_source(name: str, *, api: bool) -> str:
    methods = "\n\n".join(
        f"    async def {action}(self, request: Request):\n        return Json({payload})"
        for action, payload in _RESOURCE_ACTIONS
        if not (api and action in _FORM_ACTIONS)
    )
    return _RESOURCE_CONTROLLER_TEMPLATE.format(doc_name=name, name=name, methods=methods)


_MODULE_CONTROLLER_METHODS: tuple[tuple[str, str, bool], ...] = (
    # (action, Json placeholder payload, validates through the module request)
    ("index", '{"items": []}', False),
    ("create", '{"form": "create"}', False),
    ("store", '{"created": True}', True),
    ("show", '{"item": None}', False),
    ("edit", '{"form": "edit"}', False),
    ("update", '{"updated": True}', True),
    ("destroy", '{"deleted": True}', False),
)

_MODULE_CONTROLLER_TEMPLATE = '''"""{doc_name} API controller — thin: validate, delegate, respond."""

from fastplace.http import Controller, Json, Request

from app.modules.{module}.http.requests.store_{module}_request import Store{name}Request
from app.modules.{module}.services.{module}_service import {name}Service


class {name}Controller(Controller):
    service = {name}Service()

{methods}'''


def _module_controller_source(name: str, module: str, *, actions: frozenset[str]) -> str:
    methods = []
    for action, payload, validates in _MODULE_CONTROLLER_METHODS:
        if action not in actions:
            continue
        if validates:
            methods.append(
                f"    async def {action}(self, request: Request):\n"
                f"        data = await request.validate(Store{name}Request)\n"
                f"        return Json({payload})"
            )
        else:
            methods.append(
                f"    async def {action}(self, request: Request):\n        return Json({payload})"
            )
    return _MODULE_CONTROLLER_TEMPLATE.format(
        doc_name=name, name=name, module=module, methods="\n\n".join(methods)
    )


_MODULE_REQUEST_TEMPLATE = '''"""{doc_name} store/update request — HTTP-edge validation."""

from pydantic import BaseModel, Field


class Store{name}Request(BaseModel):
    name: str = Field(min_length=1, max_length=255)
'''


def _module_request_source(name: str) -> str:
    return _MODULE_REQUEST_TEMPLATE.format(doc_name=name, name=name)


_MODULE_PAGE_CONTROLLER_TEMPLATE = '''"""{doc_name} bridge page controller."""

from fastplace.http import Controller, Request, render


class {name}PageController(Controller):
    async def index(self, request: Request):
        return render(request, component="{name}/Index", props={{"items": []}})
'''


def _module_page_controller_source(name: str) -> str:
    return _MODULE_PAGE_CONTROLLER_TEMPLATE.format(doc_name=name, name=name)


_MODULE_ROUTES_TEMPLATE = '''"""{doc_name} module routes — auto-merged at boot (web at root, api under /api/v1)."""

from fastplace.http import Router

from app.modules.{module}.http.controllers.{module}_controller import {name}Controller
{page_import}
{web_block}
api_routes = Router()
{registrations}'''


def _module_routes_source(
    name: str, module: str, *, api_actions: tuple[str, ...], web: bool
) -> str:
    plural = _plural(module)
    registrations = [
        f'api_routes.get("/{plural}", {name}Controller, "index", name="api.{module}.index")'
    ]
    # (action, HTTP method, path shape) — id-bound actions get /{id}.
    route_by_action = {
        "create": ("get", "/create"),
        "store": ("post", ""),
        "show": ("get", "/{id}"),
        "edit": ("get", "/{id}/edit"),
        "update": ("put", "/{id}"),
        "destroy": ("delete", "/{id}"),
    }
    for action in api_actions:
        if action == "index":
            continue
        method, suffix = route_by_action[action]
        registrations.append(
            f'api_routes.{method}("/{plural}{suffix}", {name}Controller, "{action}", '
            f'name="api.{module}.{action}")'
        )
    if web:
        page_import = (
            f"from app.modules.{module}.http.controllers.{module}_page_controller import "
            f"{name}PageController"
        )
        web_block = (
            f"web_routes = Router()\n"
            f'web_routes.get("/{plural}", {name}PageController, "index", name="{module}.index")'
        )
    else:
        page_import = ""
        web_block = "web_routes: Router | None = None"
    return _MODULE_ROUTES_TEMPLATE.format(
        doc_name=name,
        name=name,
        module=module,
        page_import=page_import,
        web_block=web_block,
        registrations="\n".join(registrations),
    )


@generators_app.command("make:controller")
def make_controller(
    name: str = typer.Argument(..., help="Controller name in PascalCase"),
    resource: bool = typer.Option(
        False,
        "--resource",
        help="Include the seven CRUD action stubs (index/create/store/show/edit/update/destroy).",
    ),
    api: bool = typer.Option(
        False,
        "--api",
        help="API resource — CRUD stubs without the create/edit form actions.",
    ),
) -> None:
    """Create a controller stub in app/http/controllers/."""
    root = _project_root()
    # "ProductController" must not double the suffix in the filename — only
    # the class name keeps whatever the user passed.
    stem = name[: -len("Controller")] if name.endswith("Controller") else name
    path = root / "app" / "http" / "controllers" / f"{_snake(stem)}_controller.py"
    if api or resource:
        source = _resource_controller_source(name, api=api)
    else:
        source = _CONTROLLER_TEMPLATE.format(doc_name=name, name=name)
    _write(path, source, root)


_SERVICE_TEMPLATE = '''"""{doc_name} service — business logic layer."""


class {name}Service:
    pass
'''


@generators_app.command("make:service")
def make_service(
    name: str = typer.Argument(..., help="Service name in PascalCase"),
    module: str = typer.Option(..., "--module", "-m", help="Bounded module name"),
) -> None:
    """Create a service stub in app/modules/<module>/services/."""
    root = _project_root()
    module = _clean_module(module)
    path = root / "app" / "modules" / module / "services" / f"{_snake(name)}_service.py"
    _write(path, _SERVICE_TEMPLATE.format(doc_name=name, name=name), root)


_REPOSITORY_TEMPLATE = '''"""{doc_name} repository — data access layer."""

from fastplace.db import db
from fastplace.orm import Model


class {name}Repository:
    model: type[Model]

    async def all(self):
        return await self.model.all()
'''


@generators_app.command("make:repository")
def make_repository(
    name: str = typer.Argument(..., help="Repository name in PascalCase"),
    module: str = typer.Option(..., "--module", "-m", help="Bounded module name"),
) -> None:
    """Create a repository stub in app/modules/<module>/repositories/."""
    root = _project_root()
    module = _clean_module(module)
    path = root / "app" / "modules" / module / "repositories" / f"{_snake(name)}_repository.py"
    _write(path, _REPOSITORY_TEMPLATE.format(doc_name=name, name=name), root)


_PAGE_TEMPLATE = """import React from "react";
import {{ usePage }} from "@fastplace/react";

// Scaffolded by `fastplace make:page {component}` — props arrive from the
// controller that renders this component via `render(request, component="{component}", props=...)`.
export default function {function_name}() {{
  const {{ props, url }} = usePage();

  return (
    <div className="p-8">
      <h1 className="text-2xl font-semibold">{component}</h1>
      <p className="mt-2 text-sm opacity-70">Served by {{url}} with bridge props:</p>
      <pre className="mt-4 text-xs">{{JSON.stringify(props, null, 2)}}</pre>
    </div>
  );
}}
"""


@generators_app.command("make:module")
def make_module(
    name: str = typer.Argument(
        ..., help="Module name (snake_case singular), e.g. order — Order/OrderModule also accepted"
    ),
    bare: bool = typer.Option(
        False,
        "--bare",
        help="Only the package directories — skip the Model/Repository/Service stubs.",
    ),
    resource: bool = typer.Option(
        False,
        "--resource",
        help="Widen the module controller to the full seven CRUD actions.",
    ),
    api: bool = typer.Option(
        False,
        "--api",
        help="Widen the module controller to CRUD without the create/edit form actions.",
    ),
    web: bool = typer.Option(
        False,
        "--web",
        help="Also scaffold the bridge page controller, React page and web routes.",
    ),
    migration: bool = typer.Option(
        False, "--migration", "-m", help="Also autogenerate a migration."
    ),
) -> None:
    """Scaffold a self-contained module: slice + http edge + routes (--bare for dirs only)."""
    root = _project_root()
    # Accept "OrderModule", "Order" and "order" — a trailing Module suffix is
    # stripped so scaffolded names match the shipped lowercase-noun modules
    # (accounts, projects, ...) rather than `order_module`.
    stem = name.strip().strip("/")
    if stem.endswith("Module"):
        stem = stem[: -len("Module")]
    clean = _snake(stem)
    if not clean or not re.fullmatch(r"[a-z][a-z0-9_]*", clean):
        console.print("[red]invalid module name[/] — use snake_case starting with a letter")
        raise typer.Exit(code=1)

    base = root / "app" / "modules" / clean
    for rel in (
        "__init__.py",
        "models/__init__.py",
        "repositories/__init__.py",
        "services/__init__.py",
    ):
        _write(base / rel, "", root)
    # Parent markers so pkgutil discovery (import_all_models) finds the model.
    for marker in (root / "app" / "__init__.py", root / "app" / "modules" / "__init__.py"):
        if not marker.exists():
            _write(marker, "", root)

    if bare:
        return

    # Full vertical slice — the same templates the sibling makers use, so the
    # CSR layering (Controllers → Services → Repositories → Models) is produced
    # by the tooling rather than left to convention.
    entity = _pascal(clean)
    _write(
        base / "models" / f"{clean}.py",
        _MODEL_TEMPLATE.format(doc_name=entity, name=entity, table=_plural(clean)),
        root,
    )
    make_service(name=entity, module=clean)
    make_repository(name=entity, module=clean)

    # Module-local http edge: controller, store request, route table. The
    # default wires only index; --api widens to CRUD minus the create/edit
    # form actions, --resource to the full seven (spec flag matrix).
    if resource or api:
        actions = frozenset(a for a, _ in _RESOURCE_ACTIONS)
        api_actions = tuple(a for a, _ in _RESOURCE_ACTIONS)
        if api:
            actions -= _FORM_ACTIONS
            api_actions = tuple(a for a in api_actions if a not in _FORM_ACTIONS)
    else:
        actions = frozenset({"index"})
        api_actions = ("index",)
    http = base / "http"
    for marker in (
        http / "__init__.py",
        http / "controllers" / "__init__.py",
        http / "requests" / "__init__.py",
    ):
        marker.parent.mkdir(parents=True, exist_ok=True)
        _write(marker, "", root)
    _write(
        http / "controllers" / f"{clean}_controller.py",
        _module_controller_source(entity, clean, actions=actions),
        root,
    )
    _write(
        http / "requests" / f"store_{clean}_request.py",
        _module_request_source(entity),
        root,
    )
    if web:
        _write(
            http / "controllers" / f"{clean}_page_controller.py",
            _module_page_controller_source(entity),
            root,
        )
        make_page(name=f"{entity}/Index.tsx")
    _write(
        base / "routes.py",
        _module_routes_source(entity, clean, api_actions=api_actions, web=web),
        root,
    )
    if migration:
        _make_migration(root, clean)
    # Echo the derived names — a surprising derivation (a plural input
    # double-pluralizing into `orderses`) is visible the moment it happens,
    # not at the first route hit.
    plural = _plural(clean)
    derived = [f"entity {entity}", f"table {plural}", f"api /api/v1/{plural}"]
    if web:
        derived.append(f"web /{plural}")
    console.print(f"\n[bold]module {clean}[/bold] — " + ", ".join(derived))


@generators_app.command("make:page")
def make_page(
    name: str = typer.Argument(
        ...,
        help='Page name as routed by the bridge, e.g. "Projects/Index" or "About"',
    ),
) -> None:
    """Create a hydrated React page under resources/js/pages/."""
    root = _project_root()

    # "Projects/Index" → pages/Projects/Index.jsx; "About" → pages/About.jsx.
    clean = name.strip("/").strip()
    if not clean or clean.endswith("."):
        console.print("[red]invalid page name[/] — use a form like Projects/Index")
        raise typer.Exit(code=1)
    suffix = ".jsx"
    if clean.endswith((".jsx", ".tsx")):
        clean, _, ext = clean.rpartition(".")
        suffix = "." + ext
    # The bridge resolves components by file path; a trailing extension is the
    # only dot form we accept, everything else cannot map to a file.
    if "." in clean:
        console.print("[red]invalid page name[/] — dots are not supported; nest with '/' instead")
        raise typer.Exit(code=1)
    if not clean or not re.fullmatch(r"[A-Za-z0-9/_-]+", clean):
        console.print("[red]invalid page name[/] — use letters, digits, '-', '_' and '/'")
        raise typer.Exit(code=1)

    path = root / "resources" / "js" / "pages" / (clean + suffix)
    function_name = _page_component_name(clean)
    _write(
        path,
        _PAGE_TEMPLATE.format(component=clean, function_name=function_name),
        root,
    )


_AGENT_TEMPLATE = '''"""{doc_name} agent — assemble and return the Agent instance."""

from fastplace.ai import Agent

from app.ai.tools.{snake}_tools import {snake}_helper


def {snake}_agent() -> Agent:
    """Build the {doc_name} agent; call from routes/ai.py and stream."""
    return Agent(
        system_prompt="You are {doc_name}, a Fastplace AI agent.",
        tools=[{snake}_helper],
    )
'''

_TOOLS_TEMPLATE = '''"""{doc_name} tool suite — @Tool handlers the agent may call.

Tools obey the CSR rule: module services only, never repositories or models.
"""

from fastplace.ai import Tool


@Tool(description="Describe what the {doc_name} tool does")
async def {snake}_helper(query: str) -> str:
    """Answer a {doc_name} query.

    Args:
        query: The question or lookup text.
    """
    # TODO: call the relevant module service and return its result.
    return f"{doc_name} received: {{query}}"
'''


@generators_app.command("make:agent")
def make_agent(
    name: str = typer.Argument(..., help="Agent name (PascalCase or snake_case)"),
) -> None:
    """Scaffold an AI agent + tool suite under app/ai/ (blueprint §9)."""
    root = _project_root()
    clean = _snake(name.strip().strip("/"))
    if not clean or not re.fullmatch(r"[a-z][a-z0-9_]*", clean):
        console.print("[red]invalid agent name[/] — use letters/digits starting with a letter")
        raise typer.Exit(code=1)
    doc_name = clean.replace("_", " ").title()

    agents_dir = root / "app" / "ai" / "agents"
    tools_dir = root / "app" / "ai" / "tools"
    vectors_dir = root / "app" / "ai" / "vectors"
    for directory, marker in (
        (agents_dir, "__init__.py"),
        (tools_dir, "__init__.py"),
        (vectors_dir, "__init__.py"),
    ):
        _write(directory / marker, "", root)
    _write(
        agents_dir / f"{clean}_agent.py",
        _AGENT_TEMPLATE.format(doc_name=doc_name, snake=clean),
        root,
    )
    _write(
        tools_dir / f"{clean}_tools.py",
        _TOOLS_TEMPLATE.format(doc_name=doc_name, snake=clean),
        root,
    )


# ---------------------------------------------------------------------------
# make:* — backend scaffolding (spec #10–#18)
# ---------------------------------------------------------------------------

_SEEDER_TEMPLATE = '''"""{name} seeder — insert fixture rows."""

from __future__ import annotations


async def run() -> None:
    """Seed {name} data."""
    # TODO: insert fixture rows via the ORM.
'''


@generators_app.command("make:seeder")
def make_seeder(
    name: str = typer.Argument(..., help="Seeder name in PascalCase"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a seeder stub in database/seeders/."""
    root = _project_root()
    clean = _clean_name(name, "seeder")
    _write(
        root / "database" / "seeders" / f"{clean}_seeder.py",
        _SEEDER_TEMPLATE.format(name=name.strip()),
        root,
        force=force,
    )


_JOB_TEMPLATE = '''"""{name} job — a queued background handler."""

from __future__ import annotations

from fastplace.queue import Job


@Job()
async def {snake}(**kwargs: object) -> None:
    """Background job {name}."""
    # TODO: perform the work — kwargs arrive verbatim from queue().dispatch().
'''


@generators_app.command("make:job")
def make_job(
    name: str = typer.Argument(..., help="Job name in PascalCase"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a queue job stub in app/jobs/."""
    root = _project_root()
    clean = _clean_name(name, "job")
    # Package marker so queue.import_jobs() finds the handler at boot.
    _write(root / "app" / "jobs" / "__init__.py", "", root)
    _write(
        root / "app" / "jobs" / f"{clean}_job.py",
        _JOB_TEMPLATE.format(name=name.strip(), snake=clean),
        root,
        force=force,
    )


_REQUEST_TEMPLATE = '''"""{name} form request — validated input via request.validate({name}Request)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class {name}Request(BaseModel):
    # TODO: declare fields with Field(...) constraints — cross-field rules
    # belong in the service layer (see make:auth's request modules).
    name: str = Field(min_length=1, max_length=255)
'''


@generators_app.command("make:request")
def make_request(
    name: str = typer.Argument(..., help="Request name in PascalCase"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a form request stub in app/http/requests/."""
    root = _project_root()
    clean = _clean_name(name, "request")
    _write(
        root / "app" / "http" / "requests" / f"{clean}_request.py",
        _REQUEST_TEMPLATE.format(name=name.strip()),
        root,
        force=force,
    )


_MIDDLEWARE_TEMPLATE = '''"""{name} middleware — wraps the HTTP stack."""

from __future__ import annotations

from fastplace.http import Middleware, Request, Response


class {name}Middleware(Middleware):
    async def handle(self, request: Request, call_next) -> Response:
        # TODO: run logic before/after the rest of the stack.
        return await call_next(request)
'''


@generators_app.command("make:middleware")
def make_middleware(
    name: str = typer.Argument(..., help="Middleware name in PascalCase"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create an HTTP middleware stub in app/http/middleware/."""
    root = _project_root()
    clean = _clean_name(name, "middleware")
    _write(
        root / "app" / "http" / "middleware" / f"{clean}_middleware.py",
        _MIDDLEWARE_TEMPLATE.format(name=name.strip()),
        root,
        force=force,
    )


_POLICY_TEMPLATE = '''"""{name} policy — ability methods follow the (user, resource) -> bool contract."""

from __future__ import annotations


class {name}Policy:
    """Authorization policy for {name}."""

    # Two homes:
    #   * app/authz/ (this file) — bind explicitly with
    #     gate.policy(Model, {name}Policy) in app/auth/gates.py.
    #   * app/modules/<module>/policies/ — regenerate with
    #     `fastplace make:policy {name} --module <module>` (the module slug,
    #     e.g. billing). Gate reads the {name}Policy attribute off the
    #     module's policies package, so no explicit bind is needed for
    #     models in that module.
    async def view_any(self, user) -> bool:
        return True

    async def view(self, user, resource) -> bool:
        return True
'''


def _write_policy_reexport(policies_dir: Path, module: str, stem: str, root: Path) -> str:
    """Create or extend the module's policies package so Gate discovers it.

    Gate._resolve_policy reads the ``<Model>Policy`` attribute off the
    ``app.modules.<m>.policies`` package — an empty ``__init__`` leaves the
    binding dead. The first policy writes a fresh ``__init__`` re-exporting
    the class; every later one APPENDS its line (the non-clobbering _write
    would skip the file and leave the new class undiscovered). Returns the
    re-exported class name.
    """
    dotted = module.replace("/", ".")
    class_name = f"{_pascal(stem)}Policy"
    line = f"from app.modules.{dotted}.policies.{stem}_policy import {class_name}\n"
    init = policies_dir / "__init__.py"
    if init.is_file():
        if line in init.read_text():
            return class_name  # already re-exported — nothing to append
        with init.open("a") as handle:
            handle.write(line)
        console.print(f"[green]re-exported[/] {class_name} in {init.relative_to(root)}")
        return class_name
    policies_dir.mkdir(parents=True, exist_ok=True)
    init.write_text(
        '"""Module policies — Gate discovers the <Model>Policy attributes here."""\n\n' + line
    )
    console.print(f"[green]created[/] {init.relative_to(root)}")
    return class_name


@generators_app.command("make:policy")
def make_policy(
    name: str = typer.Argument(..., help="Policy name in PascalCase"),
    module: str = typer.Option(
        None,
        "--module",
        help=(
            "Write to app/modules/<module>/policies/ (module slug, e.g. billing); "
            "the package re-export makes Gate discover the policy."
        ),
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create an authorization policy stub (app/authz/ by default)."""
    root = _project_root()
    clean = _clean_name(name, "policy")
    if module:
        # Gate._resolve_policy reads the <ModelName>Policy attribute off the
        # app.modules.<m>.policies package, so the policy file keeps the
        # symmetric <stem>_policy.py name and the package __init__ re-exports
        # the class. The class name derives from the cleaned stem — a
        # lowercase invocation still yields InvoicePolicy, the only spelling
        # discovery looks up.
        validated = _clean_module(module)
        policies_dir = root / "app" / "modules" / validated / "policies"
        _write_policy_reexport(policies_dir, validated, clean, root)
        _write(
            policies_dir / f"{clean}_policy.py",
            _POLICY_TEMPLATE.format(name=_pascal(clean)),
            root,
            force=force,
        )
        return
    _write(root / "app" / "authz" / "__init__.py", "", root)
    _write(
        root / "app" / "authz" / f"{clean}_policy.py",
        _POLICY_TEMPLATE.format(name=name.strip()),
        root,
        force=force,
    )


_UNIT_TEST_TEMPLATE = '''"""{name} test — pure unit, no app boot needed."""

from __future__ import annotations


def test_{snake}() -> None:
    assert True
'''


_FEATURE_TEST_TEMPLATE = '''"""{name} feature test — drives the real app through the test client.

The ``client`` fixture lives in tests/conftest.py (created by this command
if the project lacks one) and boots the actual routers over a throwaway
database.
"""

from __future__ import annotations


async def test_{snake}(client) -> None:
    response = await client.get("/")
    response.assert_ok()
'''


@generators_app.command("make:test")
def make_test(
    name: str = typer.Argument(..., help="Test subject name in PascalCase"),
    feature: bool = typer.Option(
        False, "--feature", help="Scaffold under tests/feature/ with the app client."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a test stub under tests/unit/ (or tests/feature/ with --feature).

    A plain project gets the test bootstrap too: pyproject.toml gains the
    pytest tooling block, and a scaffold-shaped project (routes/web.py plus
    the accounts model) also gets tests/conftest.py — written once, never
    overwriting an existing one. On other shapes the plugin's own fixtures
    keep working instead of being shadowed by imports that cannot resolve.
    """
    root = _project_root()
    clean = _clean_name(name, "test")
    # `InvoiceTest` names the test, not the subject — the file is
    # test_invoice.py either way, so the Test suffix never doubles up.
    subject = clean
    for suffix in ("_tests", "_test"):
        if subject.endswith(suffix) and len(subject) > len(suffix):
            subject = subject[: -len(suffix)]
            break
    folder = "feature" if feature else "unit"
    template = _FEATURE_TEST_TEMPLATE if feature else _UNIT_TEST_TEMPLATE
    _ensure_test_conftest(root)
    _write(
        root / "tests" / folder / f"test_{subject}.py",
        template.format(name=name.strip(), snake=subject),
        root,
        force=force,
    )


def _ensure_test_conftest(root: Path) -> None:
    """Write tests/conftest.py once and pytest tooling into pyproject.toml.

    The conftest template imports routes.web and the accounts model, so it
    only fits a scaffold-shaped project — dropped anywhere else it would
    shadow the fastplace plugin's working fixtures with import errors.
    Non-scaffold projects keep the plugin defaults; the pytest tooling still
    lands either way, and a hand-written bootstrap is never touched.
    """
    from fastplace.cli.auth_scaffold import _TESTS_CONFTEST_TEMPLATE, _augment_pyproject

    if not (root / "pyproject.toml").is_file():
        # Minimal project table only — _augment_pyproject appends the full
        # pytest tooling tail right after, so one code path owns that block.
        # The directory name goes through the same slugifier `fastplace new`
        # uses: "My App" must not become an invalid PEP 621 project name.
        (root / "pyproject.toml").write_text(
            _PYPROJECT_BOOTSTRAP_TEMPLATE.format(slug=_slugify_project(root.name) or "app")
        )
    _augment_pyproject(root)
    if not _has_scaffold_shape(root):
        return
    conftest = root / "tests" / "conftest.py"
    if conftest.is_file():
        return
    conftest.parent.mkdir(parents=True, exist_ok=True)
    conftest.write_text(_TESTS_CONFTEST_TEMPLATE)


def _has_scaffold_shape(root: Path) -> bool:
    """Every module the emitted conftest imports must actually exist."""
    needed = (
        root / "routes" / "web.py",
        root / "app" / "modules" / "accounts" / "models" / "user.py",
    )
    return all(path.is_file() for path in needed)


_PYPROJECT_BOOTSTRAP_TEMPLATE = """[project]
name = "{slug}"
version = "0.1.0"
description = "A Fastplace application."
"""


_SCOPE_TEMPLATE = '''"""{name} scope — composable query filters ({module} module)."""

from __future__ import annotations


class {name}Scope:
    def apply(self, query, **filters):
        # TODO: narrow the query from the filters.
        return query
'''


@generators_app.command("make:scope")
def make_scope(
    name: str = typer.Argument(..., help="Scope name in PascalCase"),
    module: str = typer.Option(..., "--module", "-m", help="Bounded module name"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a query scope stub in app/modules/<module>/models/scopes/."""
    root = _project_root()
    module = _clean_module(module)
    clean = _clean_name(name, "scope")
    _write(
        root / "app" / "modules" / module / "models" / "scopes" / f"{clean}_scope.py",
        _SCOPE_TEMPLATE.format(name=name.strip(), module=module),
        root,
        force=force,
    )


_CONFIG_TEMPLATE = '''"""{name} configuration defaults (env vars always win)."""

{const} = None  # TODO: document each setting — the default's type guides env coercion.
'''


@generators_app.command("make:config")
def make_config(
    name: str = typer.Argument(..., help="Config module name, e.g. Billing or billing"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a configuration defaults module in config/."""
    root = _project_root()
    clean = _clean_name(name, "config")
    _write(
        root / "config" / f"{clean}.py",
        _CONFIG_TEMPLATE.format(name=name.strip(), const=clean.upper()),
        root,
        force=force,
    )


_MAIL_TEMPLATE = '''"""{name} mail — build the MailMessage, send via Mail.to(...).send(...)."""

from __future__ import annotations

from fastplace.mail import MailMessage


def {snake}_mail(to: str) -> MailMessage:
    return MailMessage(subject="{name}", text="...", to=to)
'''


@generators_app.command("make:mail")
def make_mail(
    name: str = typer.Argument(..., help="Mail name in PascalCase"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a mail message builder stub in app/mail/."""
    root = _project_root()
    clean = _clean_name(name, "mail")
    _write(root / "app" / "mail" / "__init__.py", "", root)
    _write(
        root / "app" / "mail" / f"{clean}.py",
        _MAIL_TEMPLATE.format(name=name.strip(), snake=clean),
        root,
        force=force,
    )


# ---------------------------------------------------------------------------
# make:* — generic scaffolding (spec #19–#22)
# ---------------------------------------------------------------------------

_SUPPORT_CLASS_TEMPLATE = '''"""{name} — a shared support class."""

from __future__ import annotations


class {name}:
    pass
'''

_SUPPORT_ENUM_TEMPLATE = '''"""{name} — an enumerated set of values."""

from __future__ import annotations

from enum import Enum


class {name}(str, Enum):
    # TODO: declare members, e.g. ACTIVE = "active" — no placeholder values shipped.
    pass
'''

_SUPPORT_EXCEPTION_TEMPLATE = '''"""{name} — a domain exception."""

from __future__ import annotations


class {name}(Exception):
    pass
'''

_SUPPORT_INTERFACE_TEMPLATE = '''"""{name} — a structural interface."""

from __future__ import annotations

from typing import Protocol


class {name}(Protocol):
    pass
'''


def _write_support_stub(name: str, what: str, template: str, root: Path, force: bool) -> None:
    """Create a generic stub in app/support/ (the package is made on demand)."""
    clean = _clean_name(name, what)
    _write(root / "app" / "support" / "__init__.py", "", root)
    _write(
        root / "app" / "support" / f"{clean}.py",
        template.format(name=name.strip()),
        root,
        force=force,
    )


@generators_app.command("make:class")
def make_class(
    name: str = typer.Argument(..., help="Class name in PascalCase"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a plain class stub in app/support/."""
    _write_support_stub(name, "class", _SUPPORT_CLASS_TEMPLATE, _project_root(), force)


@generators_app.command("make:enum")
def make_enum(
    name: str = typer.Argument(..., help="Enum name in PascalCase"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a str-Enum stub in app/support/."""
    _write_support_stub(name, "enum", _SUPPORT_ENUM_TEMPLATE, _project_root(), force)


@generators_app.command("make:exception")
def make_exception(
    name: str = typer.Argument(..., help="Exception name in PascalCase"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create an exception stub in app/support/."""
    _write_support_stub(name, "exception", _SUPPORT_EXCEPTION_TEMPLATE, _project_root(), force)


@generators_app.command("make:interface")
def make_interface(
    name: str = typer.Argument(..., help="Interface name in PascalCase"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a Protocol interface stub in app/support/."""
    _write_support_stub(name, "interface", _SUPPORT_INTERFACE_TEMPLATE, _project_root(), force)


# ---------------------------------------------------------------------------
# make:* — frontend & AI scaffolding (spec #51–#54)
# ---------------------------------------------------------------------------

_VECTOR_STORE_TEMPLATE = '''"""{doc_name} vector store — an alternate similarity-search backend."""

from fastplace.ai.vectors import register_vector_store


@register_vector_store("{name}")
class {class_name}:
    async def search(self, model_cls, embedding, limit=10):
        # TODO: query the backing index and return the nearest rows. Select
        # this backend with AI_VECTOR_STORE = "{name}" in config/ai.py or .env.
        return []
'''


@generators_app.command("make:vector-store")
def make_vector_store(
    name: str = typer.Argument(..., help="Vector store name (snake_case or PascalCase), e.g. docs"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a vector store registration in app/ai/vectors/."""
    root = _project_root()
    clean = _clean_name(name, "vector store")
    pascal = _page_component_name(clean)
    # Package markers so import_vector_stores() finds the module at boot.
    for marker in (
        root / "app" / "__init__.py",
        root / "app" / "ai" / "__init__.py",
        root / "app" / "ai" / "vectors" / "__init__.py",
    ):
        _write(marker, "", root)
    _write(
        root / "app" / "ai" / "vectors" / f"{clean}.py",
        _VECTOR_STORE_TEMPLATE.format(
            doc_name=pascal, name=clean, class_name=f"{pascal}VectorStore"
        ),
        root,
        force=force,
    )


def _frontend_component_name(name: str, what: str) -> str:
    """Validate a React scaffold name into its PascalCase identifier."""
    clean = name.strip().strip("/")
    if not clean or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", clean):
        console.print(f"[red]invalid {what} name[/] — use letters/digits starting with a letter")
        raise typer.Exit(code=1)
    return _page_component_name(clean)


_COMPONENT_TEMPLATE = """import React from "react";

// Scaffolded by `fastplace make:component {name}` — compose into pages or
// other components. Theme tokens (bg-surface, text-ink, border-line, …) are
// Tailwind utilities defined in resources/css/app.css.
export function {name}({{ children }}) {{
  return (
    <div className="border-line bg-surface-raised text-ink rounded-lg border p-4">{{children}}</div>
  );
}}
"""


@generators_app.command("make:component")
def make_component(
    name: str = typer.Argument(..., help="Component name in PascalCase, e.g. Card"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a React component stub in resources/js/components/."""
    root = _project_root()
    component = _frontend_component_name(name, "component")
    _write(
        root / "resources" / "js" / "components" / f"{component}.jsx",
        _COMPONENT_TEMPLATE.format(name=component),
        root,
        force=force,
    )


_LAYOUT_TEMPLATE = """import React from "react";

// Scaffolded by `fastplace make:layout {name}` — pages opt in via a
// `layout = {name}` static on the page component; the bridge keeps this
// chrome mounted across navigation so its state survives page swaps.
export default function {name}({{ children }}) {{
  return (
    <div className="bg-surface text-ink min-h-dvh">
      <main className="mx-auto max-w-4xl px-6 py-10">{{children}}</main>
    </div>
  );
}}
"""


@generators_app.command("make:layout")
def make_layout(
    name: str = typer.Argument(..., help="Layout name in PascalCase, e.g. Admin"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a React layout stub (children slot) in resources/js/layouts/."""
    root = _project_root()
    # "AdminLayout" must not double the suffix in the filename — only the
    # component name keeps whatever the user passed (same rule as make:controller).
    stem = name[: -len("Layout")] if name.endswith("Layout") else name
    layout = _frontend_component_name(stem, "layout") + "Layout"
    _write(
        root / "resources" / "js" / "layouts" / f"{layout}.jsx",
        _LAYOUT_TEMPLATE.format(name=layout),
        root,
        force=force,
    )


_HOOK_TEMPLATE = """import {{ useState }} from "react";

// Scaffolded by `fastplace make:hook {name}` — shared hooks live under
// resources/js/hooks/; import them from pages, layouts, or components.
export function {name}(initial = null) {{
  const [value, setValue] = useState(initial);

  // TODO: build the hook's API and return it.
  return [value, setValue];
}}
"""


@generators_app.command("make:hook")
def make_hook(
    name: str = typer.Argument(..., help="Hook name in camelCase, e.g. useDebounce"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a React hook stub in resources/js/hooks/."""
    root = _project_root()
    # The module name doubles as the hook's identifier, so it must already be
    # valid JS — the kebab-case file style of the exemplars cannot be imported.
    clean = name.strip().strip("/")
    if not clean or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", clean):
        console.print("[red]invalid hook name[/] — use a camelCase identifier like useDebounce")
        raise typer.Exit(code=1)
    _write(
        root / "resources" / "js" / "hooks" / f"{clean}.js",
        _HOOK_TEMPLATE.format(name=clean),
        root,
        force=force,
    )


# ---------------------------------------------------------------------------
# make:* — project CLI scaffolding (spec #56)
# ---------------------------------------------------------------------------

# Written verbatim per the spec — the bootstrap loader (load_app_commands)
# expects exactly this module-level `command_app` Typer.
_COMMAND_TEMPLATE = '''import typer

command_app = typer.Typer(help="{doc_name} commands.")


@command_app.command("{snake}:run")
def run() -> None:
    """Run the {doc_name} command."""
'''


@generators_app.command("make:command")
def make_command(
    name: str = typer.Argument(..., help="Command name in PascalCase"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a CLI command module in app/commands/ (mounted at bootstrap)."""
    root = _project_root()
    clean = _clean_name(name, "command")
    # Package markers so load_app_commands() imports the module at boot.
    for marker in (root / "app" / "__init__.py", root / "app" / "commands" / "__init__.py"):
        _write(marker, "", root)
    _write(
        root / "app" / "commands" / f"{clean}_command.py",
        _COMMAND_TEMPLATE.format(doc_name=clean.replace("_", " ").title(), snake=clean),
        root,
        force=force,
    )


# ---------------------------------------------------------------------------
# fastplace new — the modular-monolith project scaffolder (blueprint §3)
# ---------------------------------------------------------------------------

_INIT_TEMPLATE = '"""{doc}."""\n'

_HOME_CONTROLLER_TEMPLATE = '''"""Home controller — the welcome bridge page."""

from __future__ import annotations

from fastplace.http import Controller, Request, render


class HomeController(Controller):
    async def index(self, request: Request):
        return render(
            request,
            component="Home/Index",
            props={{"appName": "{app_name}"}},
            title="Home",
            canonical="/",
        )
'''

_WEB_ROUTES_TEMPLATE = '''"""Web routes — bridge pages (controllers return render(...))."""

from __future__ import annotations

from app.http.controllers.home_controller import HomeController
from fastplace.http import Router

router = Router()

router.get("/", HomeController, "index", name="home")
'''

_AUTH_PAGE_CONTROLLER_TEMPLATE = '''"""Auth page controller — the guest auth pages.

GET-only page routes behind the ``guest`` route middleware: the React pages
render fully client-side and read their optional props with typed defaults.
The credential endpoints live in routes/auth.py.
"""

from __future__ import annotations

from app.modules.accounts.services.password_policy import frontend_rules
from fastplace.http import Controller, Request, render


class AuthPageController(Controller):
    # Auth pages are public but never index-worthy — every one ships noindex.
    async def login(self, request: Request):
        return render(
            request, component="Auth/Login", props={}, title="Log in", robots="noindex"
        )

    async def register(self, request: Request):
        # The register page's client-side default is "minlength: 8;" — the
        # server prop mirrors the same policy that validates the POST.
        return render(
            request,
            component="Auth/Register",
            props={"passwordRules": frontend_rules()},
            title="Register",
            robots="noindex",
        )

    async def forgot_password(self, request: Request):
        return render(
            request,
            component="Auth/ForgotPassword",
            props={},
            title="Forgot Password",
            robots="noindex",
        )

    async def reset_password(self, request: Request):
        # The email link lands on /reset-password/{token}?email=... — the
        # page reads token/email from props, never from the URL directly.
        return render(
            request,
            component="Auth/ResetPassword",
            props={
                "token": request.param("token"),
                "email": request.query("email", ""),
                "passwordRules": frontend_rules(),
            },
            title="Reset Password",
            robots="noindex",
        )

    async def verify_email(self, request: Request):
        return render(
            request,
            component="Auth/VerifyEmail",
            props={},
            title="Verify Email",
            robots="noindex",
        )

    async def confirm_password(self, request: Request):
        return render(
            request,
            component="Auth/ConfirmPassword",
            props={},
            title="Confirm Password",
            robots="noindex",
        )

    async def two_factor_challenge(self, request: Request):
        return render(
            request,
            component="Auth/TwoFactorChallenge",
            props={},
            title="Two-Factor Challenge",
            robots="noindex",
        )
'''

_DASHBOARD_CONTROLLER_TEMPLATE = '''"""Dashboard page controller — the authenticated landing page."""

from __future__ import annotations

from fastplace.http import Controller, Request, render


class DashboardController(Controller):
    async def index(self, request: Request):
        # Blank starter canvas — props arrive when the app grows real data.
        return render(
            request, component="Dashboard/Index", props={}, title="Dashboard", robots="noindex"
        )
'''

_SETTINGS_PAGES_CONTROLLER_TEMPLATE = '''"""Account settings page controller — profile and security bridge pages."""

from __future__ import annotations

from app.modules.accounts.services.password_policy import frontend_rules
from fastplace.http import Controller, Request, render


class SettingsPagesController(Controller):
    async def profile(self, request: Request):
        return render(
            request,
            component="Settings/Profile",
            props={},
            title="Profile",
            robots="noindex",
        )

    async def security(self, request: Request):
        from fastplace.config import config

        user = getattr(request, "user", None)
        passkeys_enabled = bool(
            (config("AUTH_PASSKEYS", default={}) or {}).get("enabled", False)
        )
        props = {
            "passwordRules": frontend_rules(),
            "canManageTwoFactor": bool(config("TWO_FACTOR_ENABLED", default=True)),
            "requiresConfirmation": True,
            "twoFactorEnabled": getattr(user, "two_factor_confirmed_at", None) is not None,
            "canManagePasskeys": passkeys_enabled,
            "passkeys": [],
        }
        if passkeys_enabled and user is not None:
            from fastplace.auth.passkey_guard import passkey_guard

            props["passkeys"] = await passkey_guard().list_for(user)
        return render(
            request,
            component="Settings/Security",
            props=props,
            title="Security",
            robots="noindex",
        )
'''

_SETTINGS_APPEARANCE_CONTROLLER_TEMPLATE = '''"""Settings appearance controller — the appearance settings bridge page."""

from __future__ import annotations

from fastplace.http import Controller, Request, render


class SettingsAppearanceController(Controller):
    async def index(self, request: Request):
        return render(
            request,
            component="Settings/Appearance",
            props={},
            title="Appearance",
            robots="noindex",
        )
'''

#: The auth variant of routes/web.py — the guest auth pages, the blank
#: dashboard, and the account settings pages, each behind the same route
#: middleware the sample app ships (guest / auth / verified).
_WEB_ROUTES_AUTH_TEMPLATE = '''"""Web routes — bridge pages (controllers return render(...))."""

from __future__ import annotations

from app.http.controllers.auth_page_controller import AuthPageController
from app.http.controllers.dashboard_controller import DashboardController
from app.http.controllers.home_controller import HomeController
from app.http.controllers.settings_appearance_controller import SettingsAppearanceController
from app.http.controllers.settings_pages_controller import SettingsPagesController
from fastplace.http import Router

router = Router()

router.get("/", HomeController, "index", name="home")
router.get(
    "/dashboard",
    DashboardController,
    "index",
    name="dashboard",
    middleware=["auth", "verified"],
)

# Account settings pages — gated on a verified address like every other
# authenticated surface; the settings section renders the app's
# authenticated shell and its write endpoints live in routes/auth.py.
router.get(
    "/settings/appearance",
    SettingsAppearanceController,
    "index",
    name="settings.appearance",
    middleware=["auth", "verified"],
)
router.get(
    "/settings/profile",
    SettingsPagesController,
    "profile",
    name="settings.profile",
    middleware=["auth", "verified"],
)
router.get(
    "/settings/security",
    SettingsPagesController,
    "security",
    name="settings.security",
    middleware=["auth", "verified"],
)

# Guest auth pages — anonymous GET renders behind `guest`; the credential
# POSTs live in routes/auth.py.
router.get("/login", AuthPageController, "login", name="auth.login", middleware=["guest"])
router.get("/register", AuthPageController, "register", name="auth.register", middleware=["guest"])
router.get(
    "/forgot-password", AuthPageController, "forgot_password", name="auth.forgot_password"
)
router.get(
    "/reset-password/{token}", AuthPageController, "reset_password", name="auth.reset_password"
)
router.get(
    "/email/verify", AuthPageController, "verify_email", name="auth.verify_email", middleware=["auth"]
)
router.get(
    "/user/confirm-password",
    AuthPageController,
    "confirm_password",
    name="auth.confirm_password",
    middleware=["auth"],
)
router.get(
    "/two-factor-challenge",
    AuthPageController,
    "two_factor_challenge",
    name="auth.two_factor_challenge",
    middleware=["guest"],
)
'''

_API_ROUTES_TEMPLATE = '''"""API routes — unified JSON API endpoints (mounted under /api/v1)."""

from __future__ import annotations

from fastplace.http import Router

router = Router()
'''

_AI_ROUTES_TEMPLATE = '''"""AI routes — SSE/agent endpoints (mounted under /ai)."""

from __future__ import annotations

from fastplace.http import Router

router = Router()
'''

_CONFIG_APP_TEMPLATE = '''"""Application configuration defaults (env vars always win)."""

APP_NAME = "{app_name}"
APP_ENV = "local"
# Safe by default — flip to True in .env for local debugging. The kernel also
# force-disables debug details whenever APP_ENV=production.
APP_DEBUG = False
APP_URL = "http://localhost:9000"

# Bridge + assets (dev)
VITE_DEV_URL = "http://localhost:5173"

# Signing secret for sessions/CSRF/tokens. Empty here — set in .env.
APP_KEY = ""
'''

# The auth variant of config/app.py — the scaffold keys plus the route
# middleware registry the credential routes name at declaration time. The
# kernel resolves ROUTE_MIDDLEWARE aliases eagerly at mount, so the registry
# must ship with any route that names one or boot fails.
_CONFIG_APP_AUTH_TEMPLATE = '''"""Application configuration defaults (env vars always win)."""

APP_NAME = "{app_name}"
APP_ENV = "local"
# Safe by default — flip to True in .env for local debugging. The kernel also
# force-disables debug details whenever APP_ENV=production.
APP_DEBUG = False
APP_URL = "http://localhost:9000"

# Bridge + assets (dev)
VITE_DEV_URL = "http://localhost:5173"

# Signing secret for sessions/CSRF/tokens. Empty here — set in .env.
APP_KEY = ""

# Per-route middleware aliases: alias → "dotted.path.ToMiddleware".
# Parameterized aliases parse as "name:arg1,arg2" at route declaration.
ROUTE_MIDDLEWARE = {{
    "auth": "fastplace.auth.middleware.AuthenticateMiddleware",
    "guest": "fastplace.auth.middleware.GuestMiddleware",
    "verified": "fastplace.auth.middleware.EnsureEmailVerifiedMiddleware",
    "password.confirm": "fastplace.auth.middleware.EnsurePasswordConfirmedMiddleware",
    "throttle": "fastplace.ratelimit.ThrottleMiddleware",
    "abilities": "fastplace.auth.middleware.AbilitiesMiddleware",
    "ability": "fastplace.auth.middleware.AbilityMiddleware",
    "can": "fastplace.authz.middleware.CanMiddleware",
}}

# Default HTTP middleware stack (dotted paths, outermost first).
# SharedAbilitiesMiddleware sits after ResolveUserMiddleware so request.user
# is already resolved when abilities are precomputed.
MIDDLEWARE = [
    "fastplace.auth.middleware.ResolveUserMiddleware",
    "fastplace.auth.middleware.SharedAbilitiesMiddleware",
    "fastplace.auth.middleware.CsrfMiddleware",
]
'''

# The auth variant of config/auth.py — the session guard wired to the ORM
# User the scaffold installs (the "dict" provider cannot back real logins).
_CONFIG_AUTH_ORM_TEMPLATE = '''"""Authentication guard + user-provider configuration (env vars always win)."""

AUTH_DEFAULT_GUARD = "session"

# Guard drivers: "session" (server-side session store) and "jwt" (stateless Bearer token).
AUTH_GUARDS = {
    "session": {"driver": "session"},
    "token": {"driver": "jwt", "algorithm": "HS256", "ttl": 3600, "issuer": "fastplace"},
}

# How guards resolve an identifier back to a user: through the ORM User model
# the auth scaffold installed. The in-memory "dict" driver stays available for
# tests/seeders ({"driver": "dict"}).
AUTH_PROVIDERS = {
    "users": {
        "driver": "orm",
        "model": "app.modules.accounts.models.User",
    },
}
AUTH_USER_PROVIDER = "users"

# Login lockout (the session guard's attempt limiter): after
# AUTH_LOGIN_MAX_ATTEMPTS failed attempts for one email|ip pair, the next
# attempt is locked out until AUTH_LOGIN_DECAY seconds have elapsed.
AUTH_LOGIN_MAX_ATTEMPTS = 5
AUTH_LOGIN_DECAY = 60

# Password policy in the server dialect — the "min:N" clause drives
# registration validation and the bridge pages' passwordrules translation.
PASSWORD_RULES = "min:8"

# Password reset + email verification
AUTH_PASSWORD_EXPIRE = 60  # minutes a reset token stays live
AUTH_RESET_THROTTLE = 60  # seconds between reset-link emails per address
TRUSTED_HOSTS: list[str] = []  # hosts allowed to name the origin when APP_URL is empty

# Seconds a password confirmation stays valid — three hours.
PASSWORD_TIMEOUT = 10800

# Feature flag: the two-factor management endpoints + UI.
TWO_FACTOR_ENABLED = True

# Abilities precomputed into every page payload's shared auth props.
# Zero-extra-arg abilities only — no model instance exists at props time.
# Env override is comma-separated: AUTH_SHARED_ABILITIES=view-posts,view-profile
AUTH_SHARED_ABILITIES: list[str] = []

# Passkeys (WebAuthn) — the framework routes /user/passkeys* and
# /passkeys/* when enabled (spec: framework-owned, zero app code).
# Needs the 'webauthn' extra: pip install 'fastplace[webauthn]'.
# Behind a proxy APP_URL must be the public origin (TRUSTED_HOSTS convention).
AUTH_PASSKEYS = {
    "enabled": True,  # env: APP_PASSKEYS_ENABLED
    "rp_name": None,  # default: APP_NAME
    "rp_id": None,  # default: APP_URL host
    "origins": None,  # default: [APP_URL]
    "timeout_ms": 60000,  # env: APP_PASSKEYS_TIMEOUT_MS
    "user_verification": "preferred",  # confirm ALWAYS requires UV
    "attestation": "none",  # enterprise attestation lands later
    "challenge_ttl": 300,  # env: APP_PASSKEYS_CHALLENGE_TTL
    "login_max_attempts": 5,  # env: APP_PASSKEYS_LOGIN_MAX_ATTEMPTS
}
'''

_CONFIG_DATABASE_TEMPLATE = '''"""Database configuration defaults (env vars always win)."""

# Zero-config SQLite default; swap for postgres/mysql in .env for production.
DATABASE_URL = "sqlite+aiosqlite:///./database.sqlite3"
DATABASE_DRIVER = "sqlite"
'''

_CONFIG_AI_TEMPLATE = '''"""AI configuration — model routing, embeddings, vector store selection.

API keys are never stored here; LiteLLM reads OPENAI_API_KEY /
ANTHROPIC_API_KEY & friends straight from the environment.
"""

AI_MODEL = "gpt-4o-mini"
AI_EMBEDDING_MODEL = "text-embedding-3-small"
AI_MAX_TOOL_ROUNDS = 8
AI_VECTOR_STORE = "pgvector"
'''

_CONFIG_AUTH_TEMPLATE = '''"""Authentication guard + user-provider configuration (env vars always win)."""

AUTH_DEFAULT_GUARD = "session"

# Guard drivers: "session" (server-side session store) and "jwt" (stateless Bearer token).
AUTH_GUARDS = {
    "session": {"driver": "session"},
    "token": {"driver": "jwt", "algorithm": "HS256", "ttl": 3600, "issuer": "fastplace"},
}

# How guards resolve an identifier back to a user. The default "dict" driver
# is an in-memory registry (tests/seeders). Point "users" at your model for
# real apps: {"driver": "orm", "model": "app.modules.accounts.models.User"}.
AUTH_PROVIDERS = {
    "users": {"driver": "dict"},
}
AUTH_USER_PROVIDER = "users"
'''

_ENV_TEMPLATE = """\
# Fastplace environment — env vars always win over the defaults in config/*.py.

APP_NAME={app_name}
APP_ENV=local
# Flip to true locally for verbose errors; the kernel force-disables debug
# output whenever APP_ENV=production regardless of this flag.
APP_DEBUG=true
APP_URL=http://localhost:9000
# Bind interface: run dev defaults to 127.0.0.1 (loopback only), serve to
# 0.0.0.0 (every interface, for reverse-proxy deploys).
# APP_HOST=127.0.0.1
# Bind port. Unset = 9000, and `run dev` auto-falls back to the next free
# port; setting APP_PORT (or --port) pins it strictly instead.
# APP_PORT=9000
# 32+ byte secret: signs sessions, mints JWTs, signs CSRF tokens.
# Generate: python -c 'import secrets; print(secrets.token_urlsafe(48))'
APP_KEY={app_key}

# Auth sessions — server-side store; the cookie carries only the opaque ID.
# database keeps sessions across dev reloads and multi-worker serve (the
# table is framework-owned and created lazily); memory is dev-only.
SESSION_COOKIE=fastplace_session
SESSION_LIFETIME=7200
SESSION_DRIVER=database

# Per-process cache — fine for a single local process; serve refuses
# CACHE_DRIVER=memory under APP_ENV=production (set redis there).
CACHE_DRIVER=memory
# Redis for CACHE_DRIVER=redis — keys are namespaced so the DB can be
# shared with the queue; flush() clears only this app's prefix.
# REDIS_URL=redis://localhost:6379/0
# CACHE_PREFIX=fastplace:cache:

# Database — SQLite zero-config default. Production examples:
#   DATABASE_URL=postgresql://user:pass@localhost:5432/{slug}
#   DATABASE_URL=mysql://user:pass@localhost:3306/{slug}
DATABASE_URL=sqlite+aiosqlite:///./database.sqlite3
# Statements slower than QUERY_SLOW_MS warn; one repeating
# QUERY_N1_THRESHOLD times in a request flags the N+1 pattern.
# QUERY_SLOW_MS=250
# QUERY_N1_THRESHOLD=5

# Queue — memory (dev default) or saq (production, redis-backed). The
# retry knobs govern saq jobs: attempts, execution timeout (s), backoff
# base (s), and result TTL (s).
# QUEUE_DRIVER=memory
# QUEUE_NAME=fastplace
# QUEUE_REDIS_URL=redis://localhost:6379/0
# QUEUE_TRIES=3
# QUEUE_TIMEOUT=60
# QUEUE_BACKOFF=0
# QUEUE_TTL=600
# SAQ web dashboard (OFF by default; mounts only under QUEUE_DRIVER=saq).
# QUEUE_DASHBOARD_ENABLED=false
# QUEUE_DASHBOARD_PATH=/queue-dashboard

# Broadcasting — /ws/broadcast channel fan-out. memory (default, single
# process) or redis (cross-process; reuses the queue's redis when
# BROADCAST_REDIS_URL is empty).
# BROADCAST_ENABLED=true
# BROADCAST_DRIVER=memory
# BROADCAST_REDIS_URL=
# BROADCAST_CHANNEL_PREFIX=fastplace:broadcast:

# S3 storage disk (uncomment the "s3" entry in config/storage.py and
# pip install 'fastplace[s3]').
# S3_BUCKET=app-bucket
# S3_REGION=us-east-1
# S3_ENDPOINT_URL=
# S3_PUBLIC_BASE=
# S3_PUBLIC=false

# Mail (driver: log | memory | smtp)
MAIL_DRIVER=log
MAIL_FROM_ADDRESS=hello@example.com
MAIL_FROM_NAME=Fastplace
MAIL_HOST=127.0.0.1
MAIL_PORT=2525
MAIL_USERNAME=
MAIL_PASSWORD=
MAIL_ENCRYPTION=tls

# i18n — default locale; LOCALES lists every lang/<locale>.json the app serves
# (comma-separated). Requests pick one via ?locale= or Accept-Language.
LOCALE=en
LOCALES=en
APP_LOCALE=en
APP_FALLBACK_LOCALE=en

# Bridge + assets (dev)
VITE_DEV_URL=http://localhost:5173
# Optional cache-busting suffix on built asset URLs (public/build manifest).
# ASSET_VERSION=
# Prerender (SSG) — comma-separated routes `fastplace prerender` captures.
# Precedence: --route flags, then the PRERENDER_ROUTES list in asgi.py,
# then this variable, then "/" by default.
# PRERENDER_ROUTES=/

# Logging — channels write to storage/logs/ (booted by `fastplace serve`).
# single = rotating file | daily = midnight-rotated file | stderr | null |
# stack = every channel in LOG_STACK.
# LOG_CHANNEL=single
# LOG_LEVEL=INFO
# LOG_STACK=single,daily,stderr
"""

_GITIGNORE_TEMPLATE = """\
# Python
__pycache__/
*.py[cod]
.venv/
*.egg-info/
.mypy_cache/
.ruff_cache/
.pytest_cache/

# Node
node_modules/

# Test artifacts
test-results/
playwright-report/

# Runtime output — never commit or hand-edit
storage/
public/build/
*.sqlite3
database.sqlite3

# Environment
.env
.env.*
!.env.example

# OS / IDE
.DS_Store
.idea/
.vscode/
"""

_INDEX_HTML_TEMPLATE = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{app_name}</title>
    <!-- fastplace-appearance-prepaint -->
    <script>
      (function () {{
        var mode = "system";
        try {{
          var stored = localStorage.getItem("fastplace-appearance");
          if (stored === "light" || stored === "dark") mode = stored;
        }} catch (e) {{}}
        var dark =
          mode === "dark" ||
          (mode !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
        var root = document.documentElement;
        if (mode === "light" || mode === "dark") root.setAttribute("data-theme", mode);
        else root.removeAttribute("data-theme");
        if (dark) root.classList.add("dark");
        root.style.colorScheme = dark ? "dark" : "light";
      }})();
    </script>
    <style>
      html {{
        background-color: oklch(0.985 0.005 250);
      }}
      html.dark {{
        background-color: oklch(0.19 0.02 262);
      }}
    </style>
    <!-- /fastplace-appearance-prepaint -->
    <!-- Dev-only source of truth; production HTML is rendered by the Python shell. -->
    <script type="module" src="/resources/js/main.jsx"></script>
  </head>
  <body>
    <noscript>
      <div style="margin:24px auto;max-width:560px;padding:20px 24px;border:1px solid #3f3f46;border-radius:12px;background:#18181b;color:#fafafa;font-family:system-ui,sans-serif;font-size:14px;line-height:1.6">
        <p style="margin:0 0 8px;font-weight:600">{app_name} — Home Index</p>
        <p style="margin:0">This page needs JavaScript for the full interface. Forms still submit without it: posting a form reloads the page with the result.</p>
      </div>
    </noscript>
    <div
      id="fastplace"
      data-page='{{"component":"Home/Index","props":{{}},"url":"/","version":"v1"}}'
    ></div>
  </body>
</html>
"""

_ASGI_TEMPLATE = '''"""ASGI entry point — ``uvicorn asgi:app`` (used by run dev / serve)."""

from fastplace.http import create_app

app = create_app()
'''

_PYPROJECT_TEMPLATE = """\
[project]
name = "{slug}"
version = "0.1.0"
description = "A Fastplace application"
requires-python = ">=3.12"
dependencies = [
    "{fastplace_dep}",
]

# The app itself is not a distribution — an explicit empty packages list keeps
# setuptools' flat-layout auto-discovery ("Multiple top-level packages
# discovered") from breaking `pip install -e .`.
[tool.setuptools]
packages = []
"""

_PACKAGE_JSON_TEMPLATE = """\
{{
  "name": "{slug}",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {{
    "dev": "vite",
    "build": "vite build",
    "lint": "eslint . --fix",
    "lint:check": "eslint .",
    "format": "prettier --write .",
    "format:check": "prettier --check .",
    "types": "tsc --noEmit",
    "test": "vitest run",
    "test:watch": "vitest"
  }},
  "dependencies": {{
    "@fastplace/react": "{react_dep}",
    "@radix-ui/react-avatar": "^1.2.6",
    "@radix-ui/react-checkbox": "^1.3.11",
    "@radix-ui/react-collapsible": "^1.1.20",
    "@radix-ui/react-dialog": "^1.1.23",
    "@radix-ui/react-dropdown-menu": "^2.1.24",
    "@radix-ui/react-label": "^2.1.15",
    "@radix-ui/react-navigation-menu": "^1.2.22",
    "@radix-ui/react-select": "^2.3.7",
    "@radix-ui/react-separator": "^1.1.15",
    "@radix-ui/react-slot": "^1.3.3",
    "@radix-ui/react-toggle": "^1.1.18",
    "@radix-ui/react-toggle-group": "^1.1.19",
    "@radix-ui/react-tooltip": "^1.2.16",
    "class-variance-authority": "^0.7.1",
    "clsx": "^2.1.1",
    "input-otp": "^1.5.0",
    "lucide-react": "^1.46.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "sonner": "^2.0.8",
    "tailwind-merge": "^3.7.0",
    "tw-animate-css": "^1.4.0"
  }},
  "devDependencies": {{
    "@eslint/js": "^9.14.0",
    "@tailwindcss/vite": "^4.0.0",
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.1.0",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.4",
    "eslint": "^9.14.0",
    "jsdom": "^25.0.1",
    "prettier": "^3.4.2",
    "tailwindcss": "^4.0.0",
    "typescript": "^5.7.2",
    "typescript-eslint": "^8.70.0",
    "vite": "^6.0.3",
    "vitest": "^2.1.8"
  }}
}}
"""

# NOTE: this template is written verbatim (no .format call) — braces stay single.
_VITE_CONFIG_TEMPLATE = """\
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Fastplace Vite contract (blueprint §7):
// - dev: HMR server — the bridge shell references these dev-server URLs
//   directly (no proxy hop; CORS-open while developing)
// - build: hashed assets + manifest.json into public/build/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // App-tree alias: dev, build, and vitest share this config.
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "resources/js"),
    },
  },
  root: ".",
  publicDir: "public",
  build: {
    outDir: "public/build",
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: "resources/js/main.jsx",
    },
  },
  server: {
    port: Number(process.env.VITE_PORT || 5173),
    strictPort: true,
    // The dev shell points straight at this server (see fastplace/http/assets.py);
    // no reverse proxy is needed.
    proxy: {},
  },
  test: {
    environment: "jsdom",
    include: [
      "resources/js/**/__tests__/**/*.{test,spec}.{ts,tsx,js,jsx}",
    ],
  },
});
"""

_README_TEMPLATE = """\
# {app_name}

A Fastplace application — async Python backend, React frontend, one
deployable modular monolith.

## Quick start

    python -m venv .venv && source .venv/bin/activate
    pip install -e .          # or: pip install fastplace once published
    npm install               # @fastplace/react resolves once published;
                              # until then: npm i <path-to-fastplace>/packages/react
    fastplace migrate
    fastplace run dev

## Parked form targets

The starter ships the full settings UI. These form targets are intentionally
unrouted until you wire their backends: profile update (PATCH
`/settings/profile`), account deletion (DELETE `/settings/profile`), password
change (PUT `/settings/password`), and passkeys (`/user/passkeys*`). The
login, registration, password-reset, email-verification, and two-factor flows
are fully routed. Wire the parked endpoints in your own controllers when you
need them.

## Layout

- `app/modules/<name>/` — bounded feature modules (models/, repositories/, services/)
- `app/http/controllers` — thin controllers (Controllers → Services → Repositories → Models)
- `app/jobs` — queue jobs; also the consumers of domain events
- `routes/` — thin route entry points (web.py, api.py, ai.py)
- `resources/js` — React pages, layouts, components
- `config/*.py` — configuration defaults (`.env` always wins)
"""


def _slugify_project(name: str) -> str:
    """``My Blog App`` → ``my-blog-app`` (also the target directory name)."""
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def scaffold_templates_dir() -> str:
    """The shipped frontend starter corpus — package-data next to this module.

    ``fastplace new`` walks this tree verbatim into every new project: the
    design-system components, the app/auth/settings shells, the auth pages,
    the blank dashboard, and the root tooling configs.
    """
    return str(Path(__file__).parent / "scaffold_templates")


# Binary assets cannot round-trip through read_text() — copy them byte-exact.
_BINARY_SUFFIXES = {".ico", ".png", ".jpg", ".jpeg", ".gif", ".woff", ".woff2"}


def _write_scaffold_templates(target: Path) -> None:
    """Walk the shipped starter corpus into a freshly scaffolded project.

    Every file lands at its corpus-relative path, verbatim: the design system,
    the app/auth/settings shells, the auth pages, the blank dashboard, the
    root tooling configs (tsconfig, eslint), and the public assets. Text
    files go through ``_write`` (non-clobber, consistent console output);
    binaries are byte-copied.
    """
    corpus = Path(scaffold_templates_dir())
    for src in sorted(corpus.rglob("*")):
        # .DS_Store and __pycache__/.pyc artifacts are filesystem noise, not
        # corpus — a .pyc read as text would crash the whole scaffold.
        if (
            not src.is_file()
            or src.name == ".DS_Store"
            or src.suffix == ".pyc"
            or "__pycache__" in src.parts
        ):
            continue
        rel = src.relative_to(corpus)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix in _BINARY_SUFFIXES:
            if not dest.exists():
                shutil.copyfile(src, dest)
                console.print(f"[green]created[/] {dest.relative_to(_project_root())}")
            continue
        _write(dest, src.read_text(), _project_root())


def _framework_checkout() -> Path | None:
    """The framework source checkout this CLI runs from, when it does.

    Running from a checkout (``fastplace`` importable from the repo root that
    also carries ``packages/react``), generated projects wire local ``file:``
    dependencies so ``pip install -e .`` / ``npm install`` resolve before the
    packages ever ship to PyPI/npm. From a published install, version specs.
    """
    import fastplace

    root = Path(fastplace.__file__).resolve().parents[1]
    if (root / "pyproject.toml").is_file() and (root / "packages" / "react").is_dir():
        return root
    return None


def _published_fastplace_dep() -> str:
    """The dependency spec scaffolds use when running from a published
    install: a version floor pinned to the running distribution, so a
    scaffolded app never silently tracks a future breaking release."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return f"fastplace>={version('fastplace')}"
    except PackageNotFoundError:  # pragma: no cover — bare source-tree runs
        return "fastplace>=0.1.0"


def _published_react_dep() -> str:
    """The npm pin for ``@fastplace/react``, caret-pinned to the SAME
    release as the Python floor — python and npm ship in lockstep, so the
    pin derives from the running fastplace distribution rather than a
    hand-maintained literal that drifts at the next release."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return f"^{version('fastplace')}"
    except PackageNotFoundError:  # pragma: no cover — bare source-tree runs
        return "^0.1.0"


# One rewrite, shared by `new --auth` and `make:auth` (write_auth_surface):
# the auth scaffold needs the webauthn extra (passkeys) and the queue extra
# (the mail job listener), so the scaffolded dependency points at
# fastplace[queue,webauthn] instead of the plain package. The spec after
# the name (version, marker, URL, or just a comma) is preserved verbatim;
# the spec must start with a real specifier character so a package merely
# NAMED like fastplace ("fastplace-something") never matches.
_FASTPLACE_DEP_PATTERN = re.compile(r'(?m)^(\s*)"fastplace(\[[^\]]*\])?([=<>!~@,;\s][^"]*)?"')


def _rewrite_fastplace_dep_for_auth_extras(root: Path) -> bool:
    """Point the project's ``fastplace`` dependency at the auth extras.

    The scaffold ships passkeys (the ``webauthn`` extra) AND the queue-backed
    mail listener in app/jobs/mail.py (the ``queue`` extra), so the dependency
    becomes ``fastplace[queue,webauthn]``. Handles both producer shapes
    (``"fastplace",`` from `fastplace new` and ``"fastplace>=x.y.z",``) and is
    idempotent: an already-rewritten line rewrites to itself, so the second
    call is a no-op returning False (an older ``fastplace[webauthn]``-only
    line upgrades to the pair). A pyproject without a fastplace dependency
    line is left untouched.
    """
    path = root / "pyproject.toml"
    if not path.is_file():
        return False
    content = path.read_text()
    rewritten = _FASTPLACE_DEP_PATTERN.sub(r'\1"fastplace[queue,webauthn]\3"', content, count=1)
    if rewritten == content:
        return False
    path.write_text(rewritten)
    return True


# The installer wordmark — FASTPLACE set in solid blocks. The letterforms come
# from a block figlet font, narrowed to a uniform seven columns so nine letters
# fit an 80-column terminal on one line; rows join on a uniform two-column gap
# and every glyph is padded to its full cell, so the spacing never varies with
# a row's content — solid shapes with even air between them read clean where
# outline figlet fonts blur their thin connectors.
_GLYPHS: dict[str, tuple[str, ...]] = {
    "F": ("███████", "██", "██", "█████", "██", "██", "██"),
    "A": ("  ███", " ██ ██", "██   ██", "███████", "██   ██", "██   ██", "██   ██"),
    "S": (" █████", "██   ██", "██", " █████", "     ██", "██   ██", " █████"),
    "T": ("███████", "  ██", "  ██", "  ██", "  ██", "  ██", "  ██"),
    "P": ("███████", "██   ██", "██   ██", "███████", "██", "██", "██"),
    "L": ("██", "██", "██", "██", "██", "██", "███████"),
    "C": (" █████", "██   ██", "██", "██", "██", "██   ██", " █████"),
    "E": ("███████", "██", "██", "█████", "██", "██", "███████"),
}
_BANNER = "\n".join("  ".join(_GLYPHS[ch][row].ljust(7) for ch in "FASTPLACE") for row in range(7))


def _begin_step(title: str) -> None:
    """Open a named installation phase (``● Title``)."""
    console.print(f"\n[cyan]●[/] [bold]{title}[/]")


def _step_done(text: str) -> None:
    console.print(f"  [green]✓[/] {text}")


def _step_fail(text: str) -> None:
    console.print(f"  [red]✗[/] {text}")


def _run_step_command(cmd: list[str], cwd: Path, timeout: int = 600) -> tuple[bool, str]:
    """Run one installer sub-command quietly.

    Returns ``(ok, detail)`` where detail is a short, indented tail of the
    command's own output — enough to diagnose a failure without flooding the
    installer transcript.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
        )
    except OSError as exc:
        return False, "\n".join(f"    {line}" for line in str(exc).splitlines())

    def _tail(err: str, out: str) -> str:
        noise = (err + "\n" + out).strip().splitlines()
        return "\n".join(f"    {line}" for line in [ln for ln in noise if ln.strip()][-3:])

    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # npm.cmd (the Windows shim) spawns node.exe children that inherit
        # the captured pipes — killing only the direct child leaves them
        # holding the pipe and communicate() draining forever, so on Windows
        # the whole tree goes first.
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        tail = _tail(err or "", out or "")
        return False, f"    timed out after {timeout}s" + (f"\n{tail}" if tail else "")
    if proc.returncode != 0:
        return False, _tail(err or "", out or "")
    return True, ""


def _install_dependencies(target: Path) -> bool:
    """One-shot local setup for ``fastplace new --install``.

    Creates the project virtualenv, installs the app editable (pulling
    fastplace itself), runs the migrations, then installs npm packages and
    builds the frontend assets. Every sub-step reports ✓/✗; the first
    Python-side failure stops the Python toolchain, and npm is skipped with
    a warning when it is not on PATH. Returns True only when everything ran —
    the caller shortens the ready panel on True.
    """
    venv_bin = target / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    venv_python = venv_bin / ("python.exe" if os.name == "nt" else "python")
    venv_fastplace = venv_bin / ("fastplace.exe" if os.name == "nt" else "fastplace")

    # The auth scaffold ships a `dev` extra (pytest et al) — install it up
    # front so the generated test suite runs on day one; plain projects keep
    # the bare editable install.
    pyproject = target / "pyproject.toml"
    has_dev_extra = (
        pyproject.exists() and "[project.optional-dependencies]" in pyproject.read_text()
    )
    extra = ".[dev]" if has_dev_extra else "."

    for label, cmd, timeout in (
        ("Virtual environment created", [sys.executable, "-m", "venv", ".venv"], 120),
        (
            "Python dependencies installed",
            [str(venv_python), "-m", "pip", "install", "-e", extra],
            600,
        ),
        ("Database migrated", [str(venv_fastplace), "migrate"], 300),
    ):
        ok, detail = _run_step_command(cmd, target, timeout=timeout)
        if not ok:
            _step_fail(f"{label} — finish by hand with the steps below")
            if detail:
                console.print(Text(detail, style="red"))
            return False
        _step_done(label)

    npm = shutil.which("npm")
    if npm is None:
        _step_fail("Frontend packages — npm not found on PATH, install Node.js to build")
        return False

    for label, cmd, timeout in (
        ("Frontend packages installed", [npm, "install"], 900),
        ("Frontend assets built", [npm, "run", "build"], 600),
    ):
        ok, detail = _run_step_command(cmd, target, timeout=timeout)
        if not ok:
            _step_fail(f"{label} — run npm install && npm run build later")
            if detail:
                console.print(Text(detail, style="red"))
            return False
        _step_done(label)

    return True


def _next_steps_panel(
    slug: str, *, auth: bool, installed: bool, install_failed: bool = False
) -> None:
    """The boxed finale — the shortest correct next-step list for what the
    installer actually did. A failed ``--install`` run keeps the manual steps
    but drops the celebratory green: the title says what still has to happen."""
    venv_activate = ".venv\\Scripts\\activate" if os.name == "nt" else "source .venv/bin/activate"
    if installed:
        lines = [
            f"  1. cd {slug}",
            f"  2. {venv_activate}",
            "  3. fastplace run dev",
        ]
    else:
        lines = [
            f"  1. cd {slug}",
            f"  2. python -m venv .venv && {venv_activate}",
            "  3. pip install -e .   # or: pip install fastplace once published",
            "  4. npm install && npm run build",
            "  5. fastplace migrate",
            "  6. fastplace run dev",
        ]
    if auth:
        lines += [
            "",
            "  The FIRST account at http://localhost:9000/register becomes the admin.",
        ]
    if install_failed:
        title, border = "[bold]Project created — finish setup below[/]", "yellow"
    else:
        title, border = "[bold green]Application ready[/]", "green"
    console.print(Panel("\n".join(lines), title=title, border_style=border, expand=False))
    console.print("[dim]Build something great![/]")


@generators_app.command("new")
def new_project(
    name: str = typer.Argument(..., help="Project name (letters, digits, spaces, _ and -)"),
    auth: bool | None = typer.Option(
        None,
        "--auth/--no-auth",
        help="Install the built-in authentication scaffold (prompted when omitted).",
    ),
    install: bool | None = typer.Option(
        None,
        "--install/--no-install",
        help=(
            "Create the virtualenv, install dependencies, migrate the database, "
            "and build the frontend (prompted when omitted)."
        ),
    ),
) -> None:
    """Create a new Fastplace application skeleton (blueprint §3).

    Module-first layout, SQLite-by-default env, thin routes, bootable ASGI
    entry — everything ``fastplace run dev`` expects, nothing more. With
    ``--install`` the fresh project is set up end to end, ready to run.
    """
    import secrets

    console.print(_BANNER, style="green", markup=False, highlight=False, soft_wrap=True)
    console.print("[dim]Fastplace application installer[/]")

    clean = name.strip()
    # Refuse path-shaped input up front — a silent slug rewrite would scaffold
    # somewhere other than where the user pointed.
    if not clean or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]*", clean):
        console.print(
            "[red]invalid project name[/] — use letters, digits, spaces, underscores "
            "and hyphens, starting with a letter or digit"
        )
        raise typer.Exit(code=1)

    slug = _slugify_project(clean)
    app_name = slug.replace("-", " ").title()
    if len(slug.encode()) > 200:
        # Filesystem component limits (255 bytes on common APFS/ext4) minus
        # headroom for extensions (e.g. ``.env.example``) — refuse up front
        # instead of dying mid-scaffold with an OSError traceback.
        console.print("[red]invalid project name[/] — too long (keep it under 200 characters)")
        raise typer.Exit(code=1)
    target = _project_root() / slug
    if target.exists() and not target.is_dir():
        console.print(f"[red]error[/] {slug} exists and is not a directory")
        raise typer.Exit(code=1)
    if target.exists() and any(target.iterdir()):
        console.print(f"[red]error[/] {slug}/ already exists and is not empty")
        raise typer.Exit(code=1)

    # Ask once, at creation — flag wins when given; absent flag prompts with
    # a YES default; a closed stdin (pipes, CI) falls back to YES + notice
    # so unattended runs never hang or crash on the prompt. typer.confirm
    # folds both EOF and Ctrl+C into Abort, so an interactive terminal (a
    # tty present) re-raises: a deliberate interrupt must still abort.
    if auth is None:
        try:
            auth = typer.confirm("Install the built-in authentication scaffold?", default=True)
        except Exception as exc:
            if isinstance(exc, typer.exceptions.Abort) and sys.stdin and sys.stdin.isatty():
                raise
            console.print("[yellow]no interactive terminal — defaulting to --auth[/]")
            auth = True

    # Same ask for the dependency toolchain — but the fallback is NO, not
    # YES: installing costs network and minutes, so unattended runs must not
    # opt in (only the interactive Enter default and the explicit flag can).
    if install is None:
        try:
            install = typer.confirm(
                "Install dependencies now (Python venv, pip, migrations, npm)?", default=False
            )
        except Exception as exc:
            if isinstance(exc, typer.exceptions.Abort) and sys.stdin and sys.stdin.isatty():
                raise
            console.print("[yellow]no interactive terminal — skipping dependency install[/]")
            install = False

    _begin_step("Creating application files")
    env = _ENV_TEMPLATE.format(app_name=app_name, slug=slug, app_key=secrets.token_urlsafe(48))
    env_example = _ENV_TEMPLATE.format(app_name=app_name, slug=slug, app_key="")

    # Local-install wiring: when the CLI runs from the framework checkout,
    # dependency specs point at it so installs resolve pre-publish. The npm
    # side only qualifies when packages/react carries a built dist/ (its
    # entry points there); a bare source checkout would install a package
    # nothing can resolve, so it falls back to the published registry spec.
    checkout = _framework_checkout()
    fastplace_dep = f"fastplace @ file://{checkout}" if checkout else _published_fastplace_dep()
    react_dep = _published_react_dep()
    if checkout and (checkout / "packages/react/dist/fastplace-react.js").is_file():
        react_dep = f"file:{checkout / 'packages' / 'react'}"

    # (path, content) pairs — written through _write so a re-run never
    # clobbers hand edits in existing files.
    writes: list[tuple[Path, str]] = [
        (Path("app/http/controllers/__init__.py"), _INIT_TEMPLATE.format(doc="HTTP controllers.")),
        (
            Path("app/http/controllers/home_controller.py"),
            _HOME_CONTROLLER_TEMPLATE.format(app_name=app_name),
        ),
        (
            Path("app/http/requests/__init__.py"),
            _INIT_TEMPLATE.format(doc="Form requests (validated input)."),
        ),
        (Path("app/http/middleware/__init__.py"), _INIT_TEMPLATE.format(doc="HTTP middleware.")),
        (
            Path("app/modules/__init__.py"),
            _INIT_TEMPLATE.format(doc="Bounded feature modules (module-first)."),
        ),
        (Path("app/ai/agents/__init__.py"), _INIT_TEMPLATE.format(doc="AI agents.")),
        (
            Path("app/ai/tools/__init__.py"),
            _INIT_TEMPLATE.format(doc="AI tools (@Tool functions)."),
        ),
        (
            Path("app/ai/vectors/__init__.py"),
            _INIT_TEMPLATE.format(doc="Vector store registrations."),
        ),
        (
            Path("app/jobs/__init__.py"),
            _INIT_TEMPLATE.format(doc="Background jobs — domain-event consumers live here."),
        ),
        (Path("app/models/__init__.py"), _INIT_TEMPLATE.format(doc="Shared base model classes")),
        (Path("database/seeders/.gitkeep"), ""),
        (Path("routes/__init__.py"), _INIT_TEMPLATE.format(doc="Thin route entry points.")),
        (Path("routes/web.py"), _WEB_ROUTES_TEMPLATE),
        (Path("routes/api.py"), _API_ROUTES_TEMPLATE),
        (Path("routes/ai.py"), _AI_ROUTES_TEMPLATE),
        (
            Path("config/__init__.py"),
            _INIT_TEMPLATE.format(doc="Configuration defaults (env vars always win)."),
        ),
        (Path("config/app.py"), _CONFIG_APP_TEMPLATE.format(app_name=app_name)),
        (Path("config/database.py"), _CONFIG_DATABASE_TEMPLATE),
        (Path("config/ai.py"), _CONFIG_AI_TEMPLATE),
        (Path("config/auth.py"), _CONFIG_AUTH_TEMPLATE),
        (Path("public/.gitkeep"), ""),
        (Path("storage/.gitkeep"), ""),
        (
            Path("lang/en.json"),
            json.dumps({"messages": {"welcome": f"Welcome to {app_name}"}}, indent=2) + "\n",
        ),
        (Path(".env"), env),
        (Path(".env.example"), env_example),
        (Path(".gitignore"), _GITIGNORE_TEMPLATE),
        (Path("index.html"), _INDEX_HTML_TEMPLATE.format(app_name=app_name)),
        (Path("asgi.py"), _ASGI_TEMPLATE),
        (
            Path("pyproject.toml"),
            _PYPROJECT_TEMPLATE.format(slug=slug, fastplace_dep=fastplace_dep),
        ),
        (Path("package.json"), _PACKAGE_JSON_TEMPLATE.format(slug=slug, react_dep=react_dep)),
        (Path("vite.config.js"), _VITE_CONFIG_TEMPLATE),
        (Path("README.md"), _README_TEMPLATE.format(app_name=app_name)),
    ]
    for rel, content in writes:
        _write(target / rel, content, _project_root())

    # The complete frontend starter (R2): design system, shells, auth pages,
    # blank dashboard, tooling configs — every corpus file, verbatim.
    _write_scaffold_templates(target)
    _step_done("Application created")

    _begin_step("Preparing database")
    # Pre-configure the Alembic environment (what ``db:configure`` scaffolds)
    # so the printed ``fastplace migrate`` step works on a fresh project.
    from fastplace.orm.migrations import MigrationsManager

    for path in MigrationsManager(target).scaffold():
        console.print(f"[green]created[/] {path.relative_to(_project_root())}")
    _step_done("Migrations configured")

    if auth:
        _begin_step("Installing authentication")
        from fastplace.cli.auth_scaffold import write_auth_surface

        # The two config variants replace the minimal ones written moments
        # ago by this same command (force=True is safe — never hand edits).
        _write(
            target / "config/app.py",
            _CONFIG_APP_AUTH_TEMPLATE.format(app_name=app_name),
            _project_root(),
            force=True,
        )
        _write(target / "config/auth.py", _CONFIG_AUTH_ORM_TEMPLATE, _project_root(), force=True)
        write_auth_surface(target)

        # The auth variant's page layer — the guest auth pages, the blank
        # dashboard, and the settings pages. web.py replaces the minimal
        # one written moments ago by this same command (force=True is safe —
        # never hand edits); the controllers are new files.
        _write(
            target / "app/http/controllers/auth_page_controller.py",
            _AUTH_PAGE_CONTROLLER_TEMPLATE,
            _project_root(),
        )
        _write(
            target / "app/http/controllers/dashboard_controller.py",
            _DASHBOARD_CONTROLLER_TEMPLATE,
            _project_root(),
        )
        _write(
            target / "app/http/controllers/settings_pages_controller.py",
            _SETTINGS_PAGES_CONTROLLER_TEMPLATE,
            _project_root(),
        )
        _write(
            target / "app/http/controllers/settings_appearance_controller.py",
            _SETTINGS_APPEARANCE_CONTROLLER_TEMPLATE,
            _project_root(),
        )
        _write(target / "routes/web.py", _WEB_ROUTES_AUTH_TEMPLATE, _project_root(), force=True)
        _step_done("Authentication installed")

        # The users table ships with the scaffold — `fastplace migrate` on a
        # fresh project creates it, no make:auth follow-up needed.
        # Autogenerate runs in a subprocess: env.py's model discovery imports
        # every app.* module of the project it generates for, and in-process
        # those modules would stay cached in sys.modules — shadowing the host
        # project's own for the rest of this CLI process's life (duplicate
        # declarative classes, wrong gates on every later boot).
        bootstrap = (
            "from pathlib import Path\n"
            "from fastplace.orm.migrations import MigrationsManager\n"
            "rev = MigrationsManager(Path('.')).make('create_users_table')\n"
            "print(rev if rev else '')\n"
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-c", bootstrap],
                cwd=target,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=180,
            )
            made = proc.stdout.strip().splitlines()[-1].strip() if proc.stdout.strip() else ""
            if proc.returncode == 0 and made:
                _step_done(f"Users table migration created ({Path(made).name})")
                bootstrap_error = ""
            else:
                bootstrap_error = (proc.stderr or "").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            bootstrap_error = str(exc)
        if bootstrap_error:
            _step_fail("Users table migration failed")
            console.print(Text(bootstrap_error, style="red"))

    if install:
        _begin_step("Installing dependencies")
        installed = _install_dependencies(target)
    else:
        installed = False

    _next_steps_panel(
        slug, auth=auth, installed=installed, install_failed=install and not installed
    )
