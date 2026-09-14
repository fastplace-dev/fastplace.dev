"""Code generators — make:model, make:controller, make:service, make:page…"""

from __future__ import annotations

import re
from pathlib import Path

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


def _write(path: Path, content: str, root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        console.print(f"[yellow]exists[/] {path.relative_to(root)}")
        return
    path.write_text(content)
    console.print(f"[green]created[/] {path.relative_to(root)}")


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
) -> None:
    """Create an ORM model in app/modules/<module>/models/."""
    from fastplace.cli.database import _manager
    from fastplace.orm.fields import resolve_annotation  # noqa: F401 — import sanity

    root = _project_root()
    module = name[0].lower() + name[1:]
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

    if migration:
        manager = _manager()
        if not manager.configured:
            manager.scaffold()  # make:model -m works on a fresh project
        revision = manager.make(f"create_{_plural(module)}_table")
        if revision is not None:
            console.print(f"[green]created[/] {revision.relative_to(root)}")


_CONTROLLER_TEMPLATE = '''"""{doc_name} controller."""

from fastplace.http import Controller, Json, Request


class {name}(Controller):
    async def index(self, request: Request):
        return Json({{"items": []}})
'''


@generators_app.command("make:controller")
def make_controller(
    name: str = typer.Argument(..., help="Controller name in PascalCase"),
) -> None:
    """Create a controller stub in app/http/controllers/."""
    root = _project_root()
    path = root / "app" / "http" / "controllers" / f"{_snake(name)}_controller.py"
    _write(path, _CONTROLLER_TEMPLATE.format(doc_name=name, name=name), root)


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
    name: str = typer.Argument(..., help="Bounded module name (snake_case)"),
) -> None:
    """Scaffold a bounded module with the CSR layout under app/modules/."""
    root = _project_root()
    # Accept PascalCase too ("Knowledge" → "knowledge") like the other makers.
    clean = _snake(name.strip().strip("/"))
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
