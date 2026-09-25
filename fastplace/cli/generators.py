"""Code generators — make:model, make:controller, make:service, make:page…"""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

import typer

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
    module = _clean_module(name[0].lower() + name[1:])
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

    # Bind to a model with gate.policy(Model, {name}Policy) in app/auth/gates.py.
    async def view_any(self, user) -> bool:
        return True

    async def view(self, user, resource) -> bool:
        return True
'''


@generators_app.command("make:policy")
def make_policy(
    name: str = typer.Argument(..., help="Policy name in PascalCase"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create an authorization policy stub in app/authz/."""
    root = _project_root()
    clean = _clean_name(name, "policy")
    _write(root / "app" / "authz" / "__init__.py", "", root)
    _write(
        root / "app" / "authz" / f"{clean}_policy.py",
        _POLICY_TEMPLATE.format(name=name.strip()),
        root,
        force=force,
    )


_TEST_TEMPLATE = '''"""{name} test."""

from __future__ import annotations


async def test_{snake}() -> None:
    assert True
'''


@generators_app.command("make:test")
def make_test(
    name: str = typer.Argument(..., help="Test subject name in PascalCase"),
    feature: bool = typer.Option(
        False, "--feature", help="Scaffold under tests/http/ instead of tests/unit/."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a test stub under tests/unit/ (or tests/http/ with --feature)."""
    root = _project_root()
    clean = _clean_name(name, "test")
    folder = "http" if feature else "unit"
    _write(
        root / "tests" / folder / f"test_{clean}.py",
        _TEST_TEMPLATE.format(name=name.strip(), snake=clean),
        root,
        force=force,
    )


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
    <div className="border-line bg-surface-raised text-ink rounded-lg border p-4">
      {{children}}
    </div>
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
        return render(request, component="Home/Index", props={{"appName": "{app_name}"}})
'''

_WEB_ROUTES_TEMPLATE = '''"""Web routes — bridge pages (controllers return render(...))."""

from __future__ import annotations

from app.http.controllers.home_controller import HomeController
from fastplace.http import Router

router = Router()

router.get("/", HomeController, "index", name="home")
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
APP_URL = "http://localhost:8000"

# Bridge + assets (dev)
VITE_DEV_URL = "http://localhost:5173"

# Signing secret for sessions/CSRF/tokens. Empty here — set in .env.
APP_KEY = ""
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
APP_URL=http://localhost:8000
# 32+ byte secret: signs sessions, mints JWTs, signs CSRF tokens.
# Generate: python -c 'import secrets; print(secrets.token_urlsafe(48))'
APP_KEY={app_key}

# Auth sessions — server-side store; the cookie carries only the opaque ID.
SESSION_COOKIE=fastplace_session
SESSION_LIFETIME=7200

# Database — SQLite zero-config default. Production examples:
#   DATABASE_URL=postgresql://user:pass@localhost:5432/{slug}
#   DATABASE_URL=mysql://user:pass@localhost:3306/{slug}
DATABASE_URL=sqlite+aiosqlite:///./database.sqlite3

# Bridge + assets (dev)
VITE_DEV_URL=http://localhost:5173
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
.env.bak

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
    <!-- Dev-only source of truth; production HTML is rendered by the Python shell. -->
    <script type="module" src="/resources/js/main.jsx"></script>
  </head>
  <body>
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
    "build": "vite build"
  }},
  "dependencies": {{
    "@fastplace/react": "{react_dep}",
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  }},
  "devDependencies": {{
    "@tailwindcss/vite": "^4.0.0",
    "@vitejs/plugin-react": "^4.3.4",
    "tailwindcss": "^4.0.0",
    "vite": "^6.0.3"
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
});
"""

_MAIN_JSX_TEMPLATE = """\
import { createFastplaceApp, createPageResolver } from "@fastplace/react";
import "../css/app.css";

// Glob-declared pages: every resources/js/pages/**/*.{jsx,tsx} file is a
// routable component, resolved by the payload's `component` name. The glob
// is eager so pages can expose their persistent layout through a `layout`
// static before first render.
const resolvePage = createPageResolver(
  import.meta.glob("./pages/**/*.{jsx,tsx,js,ts}", { eager: true }),
);

createFastplaceApp({ resolve: resolvePage }).catch((err) => {
  console.error("[fastplace] bootstrap failed:", err);
  const el = document.getElementById("fastplace");
  if (el) {
    el.textContent = "Failed to boot the Fastplace app — check the browser console.";
  }
});
"""

_APP_LAYOUT_TEMPLATE = """\
import React from "react";
import { Link, usePage } from "@fastplace/react";

/**
 * The persistent application chrome — header + content slot. Pages opt in
 * via a `layout = AppLayout` static; the bridge keeps this component
 * mounted across navigation, so its state survives page swaps.
 */
export default function AppLayout({ children }) {
  const { props } = usePage();

  return (
    <div className="min-h-dvh bg-surface text-ink">
      <header className="border-line bg-surface-raised border-b">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-3">
          <Link href="/" className="text-lg font-semibold">
            {props.appName ?? "Fastplace"}
          </Link>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-6 py-10">{children}</main>
    </div>
  );
}
"""

_HOME_PAGE_TEMPLATE = """\
import React from "react";
import { usePage } from "@fastplace/react";
import AppLayout from "../../layouts/AppLayout";

export default function HomeIndex() {
  const { props } = usePage();

  return (
    <section>
      <h1 className="text-2xl font-semibold">{props.appName ?? "Fastplace"}</h1>
      <p className="text-ink-muted mt-2">
        Your Fastplace app is running. Edit{" "}
        <code>resources/js/pages/Home/Index.jsx</code> to get started.
      </p>
    </section>
  );
}

// Persistent-layout opt-in — the bridge reads this static on the component.
HomeIndex.layout = AppLayout;
"""

_APP_CSS_TEMPLATE = """\
/*
 * Fastplace global styles — the single source of truth for the color theme.
 * Components use these tokens via Tailwind utility classes (bg-surface,
 * text-ink, border-line, …).
 */
@import "tailwindcss";

@theme {
  --color-surface: oklch(0.985 0.002 250);
  --color-surface-raised: oklch(1 0 0);
  --color-ink: oklch(0.2 0.02 258);
  --color-ink-muted: oklch(0.5 0.02 258);
  --color-line: oklch(0.9 0.01 258);
  --color-brand-500: oklch(0.62 0.17 258);
  --color-brand-600: oklch(0.55 0.18 258);
}
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


@generators_app.command("new")
def new_project(
    name: str = typer.Argument(..., help="Project name (letters, digits, spaces, _ and -)"),
) -> None:
    """Create a new Fastplace application skeleton (blueprint §3).

    Module-first layout, SQLite-by-default env, thin routes, bootable ASGI
    entry — everything ``fastplace run dev`` expects, nothing more.
    """
    import secrets

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

    env = _ENV_TEMPLATE.format(app_name=app_name, slug=slug, app_key=secrets.token_urlsafe(48))
    env_example = _ENV_TEMPLATE.format(app_name=app_name, slug=slug, app_key="")

    # Local-install wiring: when the CLI runs from the framework checkout,
    # dependency specs point at it so installs resolve pre-publish.
    checkout = _framework_checkout()
    fastplace_dep = f"fastplace @ file://{checkout}" if checkout else _published_fastplace_dep()
    react_dep = f"file:{checkout / 'packages' / 'react'}" if checkout else "^0.1.0"

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
        (Path("app/models/__init__.py"), _INIT_TEMPLATE.format(doc="Shared base model classes.")),
        (Path("database/seeders/.gitkeep"), ""),
        (Path("resources/js/main.jsx"), _MAIN_JSX_TEMPLATE),
        (Path("resources/js/layouts/AppLayout.jsx"), _APP_LAYOUT_TEMPLATE),
        (Path("resources/js/pages/Home/Index.jsx"), _HOME_PAGE_TEMPLATE),
        (Path("resources/js/components/.gitkeep"), ""),
        (Path("resources/js/hooks/.gitkeep"), ""),
        (Path("resources/css/app.css"), _APP_CSS_TEMPLATE),
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

    # Pre-configure the Alembic environment (what ``db:configure`` scaffolds)
    # so the printed ``fastplace migrate`` step works on a fresh project.
    from fastplace.orm.migrations import MigrationsManager

    for path in MigrationsManager(target).scaffold():
        console.print(f"[green]created[/] {path.relative_to(_project_root())}")

    console.print("\n[green]Fastplace app ready![/] Next steps:\n")
    console.print(f"  cd {slug}")
    console.print("  python -m venv .venv && source .venv/bin/activate")
    console.print("  pip install -e .   # or: pip install fastplace once published")
    console.print("  npm install")
    console.print("  fastplace migrate")
    console.print("  fastplace run dev\n")
