"""Inspection commands — the project's routes and effective configuration."""

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
