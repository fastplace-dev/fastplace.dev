"""Inspection commands — the project's routes, configuration, and models."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import typer

from fastplace.console import console

inspect_app = typer.Typer(help="Inspect project routes and configuration.")

#: Top-level module names a Fastplace project owns on ``sys.path``. They —
#: and only they — are snapshotted around the in-process ASGI import so one
#: project's code never shadows another's later in the process.
_PROJECT_NAMESPACES = frozenset({"asgi", "routes", "app"})


def _project_root() -> Path:
    """The cwd when it is a Fastplace project; a friendly exit otherwise."""
    root = Path.cwd()
    if not (root / "asgi.py").is_file():
        console.print(
            "[red]not inside a Fastplace project[/] — run this from a project root "
            "(the directory containing asgi.py)."
        )
        raise typer.Exit(code=1)
    return root


def _load_asgi_app(root: Path) -> Any:
    """Import ``asgi:app`` the way ``fastplace run dev`` targets it.

    The project root goes to the front of ``sys.path`` (uvicorn's cwd
    behavior, in-process). Cached modules in the project namespaces are
    snapshotted and restored around the import, so booting one project
    never leaves it hijacking ``import asgi``/``routes``/``app`` for the
    rest of the process.
    """
    root_str = str(root)
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name.split(".", 1)[0] in _PROJECT_NAMESPACES
    }
    for name in saved:
        sys.modules.pop(name)
    inserted = root_str not in sys.path
    if inserted:
        sys.path.insert(0, root_str)
    try:
        module = importlib.import_module("asgi")
        return module.app
    finally:
        if inserted:
            sys.path.remove(root_str)
        for name in [n for n in sys.modules if n.split(".", 1)[0] in _PROJECT_NAMESPACES]:
            sys.modules.pop(name)
        sys.modules.update(saved)


def _iter_routes(routes: list[Any]) -> Iterator[Any]:
    """Flatten an app's route tree into plain route objects.

    Newer FastAPI wraps ``include_router`` output in an internal
    ``_IncludedRouter`` node holding the real routes on ``original_router``
    (older versions flatten them straight into ``app.routes``); duck-type on
    the attribute so both layouts — and nested includes — walk the same way.
    """
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _iter_routes(list(getattr(included, "routes", [])))
            continue
        yield route


def collect_routes() -> list[dict[str, str]]:
    """Every route on the project's ASGI app, as ``{method, path, name}`` dicts.

    Methods join in sorted order (``GET,POST``); websockets report ``WS``.
    Mounted static file systems are not endpoint routes and stay hidden.
    """
    app = _load_asgi_app(_project_root())

    from starlette.routing import Route as StarletteRoute
    from starlette.routing import WebSocketRoute

    rows: list[dict[str, str]] = []
    for route in _iter_routes(list(getattr(app, "routes", []))):
        if isinstance(route, WebSocketRoute):
            rows.append({"method": "WS", "path": route.path, "name": route.name or ""})
        elif isinstance(route, StarletteRoute):
            methods = ",".join(sorted(getattr(route, "methods", None) or [])) or "*"
            rows.append({"method": methods, "path": route.path, "name": route.name or ""})
    rows.sort(key=lambda row: (row["path"], row["method"]))
    return rows


@inspect_app.command("route:list")
def route_list(
    path: str = typer.Option(None, "--path", help="Only routes whose path contains this substring."),
    method: str = typer.Option(
        None, "--method", help="Only routes whose methods include this one (e.g. GET)."
    ),
    name: str = typer.Option(None, "--name", help="Only routes whose name contains this substring."),
) -> None:
    """List the project's HTTP and WebSocket routes."""
    rows = collect_routes()
    if path is not None:
        rows = [row for row in rows if path.lower() in row["path"].lower()]
    if method is not None:
        rows = [row for row in rows if method.upper() in row["method"].upper()]
    if name is not None:
        rows = [row for row in rows if name.lower() in row["name"].lower()]

    if not rows:
        console.print("[dim]no routes matched the given filters[/]")
        return

    from rich.table import Table

    table = Table(title="Fastplace routes")
    table.add_column("method", style="bold cyan", no_wrap=True)
    table.add_column("path")
    table.add_column("name", style="dim")
    for row in rows:
        table.add_row(row["method"], row["path"], row["name"])
    console.print(table)


def _declared_in_project(cls: Any, root: Path) -> bool:
    """True when ``cls`` is a mapped model whose source module lives under root.

    Path math against ``__module__`` — never ``sys.modules``, whose entries for
    an earlier project may have been evicted from this process — plus a
    ``__table__`` check that keeps half-mapped shadows (a re-import that
    collided with tables already registered here) out of listings.
    """
    parts = cls.__module__.split(".")
    if parts[0] != "app" or getattr(cls, "__table__", None) is None:
        return False
    base = root.joinpath(*parts)
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def collect_models(root: Path | None = None) -> list[Any]:
    """The project's mapped Model subclasses, scoped to files under ``root``.

    ``import_all_models`` (the registry's import-all entry) runs first; an
    InvalidRequestError means this process mapped these modules before and
    the already-registered classes ARE the project's — nothing to re-import.
    """
    from sqlalchemy.exc import InvalidRequestError

    from fastplace.orm.registry import all_models, import_all_models

    base = Path.cwd() if root is None else root
    root_str = str(base)
    inserted = root_str not in sys.path
    if inserted:
        sys.path.insert(0, root_str)
    try:
        try:
            import_all_models(base)
        except InvalidRequestError:
            pass  # tables already mapped in-process; the existing classes stand in
    finally:
        if inserted:
            sys.path.remove(root_str)
    return sorted(
        (cls for cls in all_models() if _declared_in_project(cls, base)),
        key=lambda cls: (cls.__module__, cls.__name__),
    )


@inspect_app.command("model:list")
def model_list() -> None:
    """List the project's models (module, class, table)."""
    models = collect_models(_project_root())
    if not models:
        console.print("[dim]no models declared — add one with `fastplace make:model Name`[/]")
        return

    from rich.table import Table

    table = Table(title="Fastplace models")
    table.add_column("module", style="dim")
    table.add_column("class", style="bold cyan", no_wrap=True)
    table.add_column("table", no_wrap=True)
    for cls in models:
        table.add_row(cls.__module__, cls.__name__, cls.__tablename__)
    console.print(table)


@inspect_app.command("model:show")
def model_show(
    name: str = typer.Argument(..., help="Model class name, e.g. User."),
) -> None:
    """Show one model's table, fields (with types), and relationships."""
    from sqlalchemy import inspect as sa_inspect

    models = collect_models(_project_root())
    cls = next((candidate for candidate in models if candidate.__name__ == name), None)
    if cls is None:
        if models:
            known = ", ".join(sorted(candidate.__name__ for candidate in models))
            console.print(f"[red]unknown model[/] {name!r} — known models: {known}")
        else:
            console.print(f"[red]unknown model[/] {name!r} — none declared in this project.")
        raise typer.Exit(code=1)

    console.print(
        f"[bold]{cls.__module__}.{cls.__name__}[/] — table [bold cyan]{cls.__tablename__}[/]"
    )

    from rich.table import Table

    fields = Table(title="Fields")
    fields.add_column("field", style="bold")
    fields.add_column("type", no_wrap=True)
    fields.add_column("attributes", style="dim")
    for column in sa_inspect(cls).columns:
        attrs: list[str] = []
        if column.primary_key:
            attrs.append("primary key")
        if column.unique:
            attrs.append("unique")
        if column.index:
            attrs.append("index")
        if column.nullable and not column.primary_key:
            attrs.append("nullable")
        fields.add_row(column.name, str(column.type), ", ".join(attrs) or "—")
    console.print(fields)

    relationships = sorted(sa_inspect(cls).relationships, key=lambda prop: prop.key)
    if not relationships:
        return
    kinds = {"ONETOMANY": "one-to-many", "MANYTOONE": "many-to-one", "MANYTOMANY": "many-to-many"}
    rels = Table(title="Relationships")
    rels.add_column("name", style="bold")
    rels.add_column("target", no_wrap=True)
    rels.add_column("kind", style="dim")
    for prop in relationships:
        kind = kinds.get(prop.direction.name, prop.direction.name.lower().replace("_", "-"))
        rels.add_row(prop.key, prop.mapper.class_.__name__, kind)
    console.print(rels)


@inspect_app.command("event:list")
def event_list() -> None:
    """List the domain-event listeners registered in this process."""
    from fastplace.events import registered_listeners

    _project_root()
    listeners = registered_listeners()
    if not listeners:
        console.print("[dim]no event listeners registered in this process[/]")
        return

    from rich.table import Table

    table = Table(title="Domain event listeners")
    table.add_column("event", style="bold cyan", no_wrap=True)
    table.add_column("handlers")
    for event in sorted(listeners):
        table.add_row(event, "\n".join(listeners[event]))
    console.print(table)


def _layer_mark(module_path: Path, layer: str) -> str:
    """A check for the CSR layer directory the module carries, — otherwise."""
    return "✓" if (module_path / layer).is_dir() else "—"


@inspect_app.command("module:list")
def module_list() -> None:
    """List bounded modules under app/modules and their CSR layer coverage."""
    from fastplace.modules import discover_modules

    modules = discover_modules(_project_root())
    if not modules:
        console.print("[dim]no bounded modules under app/modules/[/]")
        return

    from rich.table import Table

    table = Table(title="Bounded modules")
    table.add_column("module", style="bold cyan", no_wrap=True)
    table.add_column("models", justify="center")
    table.add_column("repositories", justify="center")
    table.add_column("services", justify="center")
    for name in sorted(modules):
        path = modules[name].path
        table.add_row(
            name,
            _layer_mark(path, "models"),
            _layer_mark(path, "repositories"),
            _layer_mark(path, "services"),
        )
    console.print(table)


@inspect_app.command("gate:list")
def gate_list() -> None:
    """List registered gate abilities and explicitly bound policies."""
    from fastplace.authz.gate import gate
    from fastplace.authz.loader import import_gates

    root = _project_root()
    import_gates(root)  # pulls in app/auth/gates.py when the project has one
    abilities = gate.registered_abilities()
    policies = gate.registered_policies()
    if not abilities and not policies:
        console.print("[dim]no gate abilities or policies registered[/]")
        return

    from rich.table import Table

    abilities_table = Table(title="Gate abilities")
    abilities_table.add_column("ability", style="bold cyan")
    for ability in abilities:
        abilities_table.add_row(ability)
    console.print(abilities_table)

    if policies:
        policies_table = Table(title="Policies (explicitly bound)")
        policies_table.add_column("model", style="bold")
        policies_table.add_column("policy")
        for model_name, policy_name in policies.items():
            policies_table.add_row(model_name, policy_name)
        console.print(policies_table)


@inspect_app.command("ai:tools")
def ai_tools() -> None:
    """List the @Tool functions registered in this process."""
    from fastplace.ai.tool import registered_tools, tool_registry

    _project_root()
    names = registered_tools()
    if not names:
        console.print(
            "[dim]no tools registered in this process — @Tool registers at import time[/]"
        )
        return

    from rich.table import Table

    table = Table(title="AI tools")
    table.add_column("name", style="bold cyan", no_wrap=True)
    table.add_column("description")
    table.add_column("params", justify="right")
    for name in names:
        spec = tool_registry[name]
        properties = spec.parameters.get("properties", {})
        table.add_row(spec.name, spec.description or "—", str(len(properties)))
    console.print(table)


@inspect_app.command("ai:vectors")
def ai_vectors() -> None:
    """List registered vector stores and mark the active one."""
    from fastplace.ai.vectors import import_vector_stores, vector_registry

    root = _project_root()
    import_vector_stores(root)  # pulls in app/ai/vectors/* when present
    if not vector_registry:
        console.print("[dim]no vector stores registered[/]")
        return

    from fastplace.config import config, load_env, reset_config

    load_env()
    reset_config().load()  # bind the config registry to this project
    active = config("AI_VECTOR_STORE", default="pgvector")
    if active not in vector_registry:
        console.print(
            f"[red]unknown vector store[/] {active!r} — AI_VECTOR_STORE must name a "
            f"registered store: {', '.join(sorted(vector_registry))}"
        )
        raise typer.Exit(code=1)

    from rich.table import Table

    table = Table(title="Vector stores")
    table.add_column("store", style="bold cyan", no_wrap=True)
    table.add_column("status")
    for name in sorted(vector_registry):
        table.add_row(name, "active" if name == active else "—")
    console.print(table)


#: A key whose uppercased name contains any of these substrings is secret.
MASK_PATTERNS = ("KEY", "SECRET", "PASSWORD", "TOKEN")

_MASK = "****"


def mask(key: str, value: str) -> str:
    """``****`` when the key looks secret; the value unchanged otherwise.

    Full mask, never a partial hint — a two-character prefix still narrows
    a brute-force search space.
    """
    upper = key.upper()
    if any(pattern in upper for pattern in MASK_PATTERNS):
        return _MASK
    return value


@inspect_app.command("config:show")
def config_show(
    key: str = typer.Argument(None, help="Show only this key's effective value."),
) -> None:
    """Show effective configuration (environment wins over config/*.py defaults)."""
    from fastplace.config import config, load_env, reset_config

    _project_root()
    load_env()
    registry = reset_config()  # bind the config registry to this project
    registry.load()

    if key is not None:
        value = config(key)
        if value is None:
            console.print(
                f"[red]unknown config key[/] {key!r} — not set in the environment "
                "or any config/*.py module."
            )
            raise typer.Exit(code=1)
        console.print(mask(key, str(value)))
        return

    from rich.table import Table

    table = Table(title="Effective configuration")
    table.add_column("key", style="bold")
    table.add_column("value")
    # _defaults carries both ``KEY`` and ``namespace.KEY`` aliases; the plain
    # form is the surface users know, the dotted ones stay lookup handles.
    for name in sorted(k for k in registry._defaults if "." not in k):
        table.add_row(name, mask(name, str(config(name))))
    console.print(table)
