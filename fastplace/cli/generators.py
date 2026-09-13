"""Code generators — make:model, make:controller, make:service, make:page…"""

from __future__ import annotations

from pathlib import Path

import typer

from fastplace.console import console

generators_app = typer.Typer(help="Generate framework scaffolding.")


def _project_root() -> Path:
    return Path.cwd()


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
        return Json(items=[])
'''


@generators_app.command("make:controller")
def make_controller(
    name: str = typer.Argument(..., help="Controller name in PascalCase"),
) -> None:
    """Create a controller stub in app/http/controllers/."""
    root = _project_root()
    path = root / "app" / "http" / "controllers" / f"{name.lower()}.py"
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
    path = root / "app" / "modules" / module / "services" / f"{name.lower()}_service.py"
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
    path = root / "app" / "modules" / module / "repositories" / f"{name.lower()}_repository.py"
    _write(path, _REPOSITORY_TEMPLATE.format(doc_name=name, name=name), root)
