"""Inspection commands — the project's routes, configuration, and models."""

from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Callable, Iterator
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
    path: str = typer.Option(
        None, "--path", help="Only routes whose path contains this substring."
    ),
    method: str = typer.Option(
        None, "--method", help="Only routes whose methods include this one (e.g. GET)."
    ),
    name: str = typer.Option(
        None, "--name", help="Only routes whose name contains this substring."
    ),
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


def _pattern_matches(pattern: str, path: str) -> bool:
    """``/items/{id}`` matches ``/items/42`` — single-segment params only.

    ``re.escape`` escapes the braces too, so the replaces un-escape exactly
    the ``{``/``}`` pair before the ``{param}`` placeholders become
    ``[^/]+``; every other regex-special character stays escaped.
    """
    regex = (
        "^"
        + re.sub(
            r"\{[^}/]+\}",
            r"[^/]+",
            re.escape(pattern).replace(r"\{", "{").replace(r"\}", "}"),
        )
        + "$"
    )
    return re.match(regex, path) is not None


def _match_route(app: Any, path: str) -> Any:
    """The booted route serving ``path`` — exact path first, pattern fallback.

    Walks the flattened route table (``_iter_routes``), so included routers
    match like ``route:list`` lists them. First match wins in both passes;
    ``None`` means nothing on the app serves the path.
    """
    candidates = list(_iter_routes(list(getattr(app, "routes", []))))
    for route in candidates:
        if getattr(route, "path", None) == path:
            return route
    for route in candidates:
        route_path = getattr(route, "path", "")
        if "{" in route_path and _pattern_matches(route_path, path):
            return route
    return None


def _declared_route(root: Path, mounted_path: str) -> Any:
    """The Fastplace ``Route`` declaring ``mounted_path``, if a router owns it.

    The booted Starlette route keeps neither the alias tuple nor the
    original handler (the endpoint adapter wraps it), so the deep-dive
    fields — aliases in declaration order, the ``module.qualname`` handler
    — are recovered from the declaring router, joined with the same prefix
    math the kernel mounts with (web/auth at the root, api under
    ``/api/v1``, ai under ``/ai``). Project namespaces snapshot/restore
    around the import — the exact discipline of ``_load_asgi_app``.
    """
    from fastplace.http.kernel import AI_PREFIX, API_PREFIX, _load_router_module

    saved = {
        name: module
        for name, module in sys.modules.items()
        if name.split(".", 1)[0] in _PROJECT_NAMESPACES
    }
    for name in saved:
        sys.modules.pop(name)
    root_str = str(root)
    inserted = root_str not in sys.path
    if inserted:
        sys.path.insert(0, root_str)
    try:
        surfaces = (
            ("routes.web", ""),
            ("routes.auth", ""),
            ("routes.api", API_PREFIX),
            ("routes.ai", AI_PREFIX),
        )
        for dotted, prefix in surfaces:
            router = _load_router_module(root, dotted)
            for declared in getattr(router, "routes", []) or []:
                if prefix + declared.path == mounted_path:
                    return declared
        return None
    finally:
        if inserted:
            sys.path.remove(root_str)
        for name in [n for n in sys.modules if n.split(".", 1)[0] in _PROJECT_NAMESPACES]:
            sys.modules.pop(name)
        sys.modules.update(saved)


@inspect_app.command("route:show")
def route_show(
    path: str = typer.Argument(
        ..., help="Route path — pattern (/items/{id}) or concrete (/items/42)."
    ),
) -> None:
    """Deep-dive one route: methods, handler, aliases, resolved middleware chain."""
    from fastplace.config import load_env, reset_config
    from fastplace.errors import ConfigurationError
    from fastplace.http.kernel import _ConfigShim, _route_middleware_registry
    from fastplace.http.router import resolve_route_middleware

    root = _project_root()
    load_env(root / ".env")
    reset_config(root)

    app = _load_asgi_app(root)
    route = _match_route(app, path)
    if route is None:
        console.print(f"[red]no route matches {path}[/] — try [bold]fastplace route:list[/].")
        raise typer.Exit(code=1)

    declared = _declared_route(root, getattr(route, "path", path))
    aliases: list[str] = list(getattr(declared, "middleware", ()) or ())
    handler = (
        getattr(declared, "handler", None)
        or getattr(route, "endpoint", None)
        or getattr(route, "app", None)
    )
    handler_name = (
        f"{getattr(handler, '__module__', '?')}.{getattr(handler, '__qualname__', '?')}"
        if handler is not None
        else "-"
    )

    registry = _route_middleware_registry({}, _ConfigShim({}, root=str(root)))
    chain: list[str] = []
    for alias in aliases:
        try:
            resolved = resolve_route_middleware(registry, alias)
        except ConfigurationError as exc:
            # The kernel resolves aliases eagerly at boot, so an unregistered
            # alias never reaches this command through create_app(); this
            # branch covers the drift case — a project booted with aliases
            # supplied programmatically (get_app(route_middleware=...)) that
            # the config-side registry built here does not know.
            chain.append(f"[red]{alias} — UNKNOWN ({exc})[/]")
            continue
        # Parameterized factories answer with a built instance — display its
        # class, never the repr with a per-process object address.
        cls = resolved[0] if isinstance(resolved, tuple) else resolved
        display = cls if isinstance(cls, type) else type(cls)
        chain.append(f"{alias} -> {display.__module__}.{display.__name__}")

    from rich.table import Table

    methods = ",".join(sorted(getattr(route, "methods", None) or [])) or "-"
    table = Table(title=f"route {path}")
    table.add_column("field", style="bold")
    table.add_column("value")
    table.add_row("mounted path", getattr(route, "path", "-"))
    table.add_row("methods", methods)
    table.add_row("name", getattr(route, "name", None) or "-")
    table.add_row("handler", handler_name)
    table.add_row("aliases (declaration order)", ", ".join(aliases) or "-")
    table.add_row("middleware chain", "\n".join(chain) or "-")
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


def _import_project_registrations(importer: Callable[[], object]) -> None:
    """Best-effort import of the project's registration modules.

    Real users run these commands in a fresh process, where importing the
    project's modules is safe and the listing is complete (listeners live in
    ``app/jobs`` registrations, tools in ``app/ai/tools``). In a reused
    process the import may be impossible — name collisions from earlier
    registrations, module-cache churn, broken project code — and a read-only
    command must never crash on that: print one dim note and fall back to
    the in-process registry view.
    """
    try:
        importer()
    except Exception:  # noqa: BLE001 — any project-import failure degrades, never crashes
        console.print(
            "[dim]project import unavailable in this process; "
            "showing in-process registrations only[/]"
        )


@inspect_app.command("event:list")
def event_list() -> None:
    """List the project's domain-event listeners (app/jobs registrations)."""
    from fastplace.events import registered_listeners
    from fastplace.queue import import_jobs

    root = _project_root()
    _import_project_registrations(lambda: import_jobs(root))  # queue:work's targeting
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
    """List the project's @Tool functions (app/ai/tools registrations)."""
    from fastplace.ai.tool import import_tools, registered_tools, tool_registry

    root = _project_root()
    _import_project_registrations(lambda: import_tools(root))
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


@inspect_app.command("search:status")
def search_status() -> None:
    """Show the active search service — which backend answers free-text queries."""
    _project_root()
    from fastplace.search import DatabaseSearchService, get_search_service

    service = get_search_service()
    cls = type(service)
    console.print(f"Active search service: [bold cyan]{cls.__name__}[/]")
    if isinstance(service, DatabaseSearchService):
        console.print(
            f"[dim]{cls.__module__} — the default; PostgreSQL full-text search. "
            "Applications swap it with register_search_service().[/]"
        )
    else:
        console.print(f"[dim]{cls.__module__}[/]")


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
