"""`fastplace prerender` — capture configured routes to static HTML."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import ModuleType
from typing import Any

import typer

from fastplace.console import console

prerender_app = typer.Typer(help="Static prerendering (SSG).")


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


def _load_app(root: Path, app_ref: str | None) -> tuple[Any, ModuleType]:
    """Import the project's ASGI app and its module — the ``asgi:app``
    convention `fastplace serve` targets, or an explicit ``module:attr``.

    Returns both because route resolution reads ``PRERENDER_ROUTES`` off
    the module the app came from.
    """
    from fastplace.orm.registry import project_boot_sandbox

    with project_boot_sandbox(root):
        if app_ref is None:
            module = importlib.import_module("asgi")
            return module.app, module
        module_name, _, attr = app_ref.partition(":")
        module = importlib.import_module(module_name)
        try:
            app = getattr(module, attr or "app")
        except AttributeError as exc:
            raise ImportError(f"{app_ref!r} has no attribute {attr or 'app'!r}") from exc
        return app, module


@prerender_app.command("prerender")
def prerender(
    route: list[str] = typer.Option(
        None, "--route", help="Route to capture (repeatable). Overrides config."
    ),
    out: Path = typer.Option(
        None, "--out", help="Output directory (default: public/build/prerender)."
    ),
    app_ref: str = typer.Option(
        None, "--app", help="App import target as module:attr (default: asgi:app)."
    ),
    timeout: float = typer.Option(
        30.0, "--timeout", help="Per-route capture timeout in seconds (default: 30)."
    ),
    force: bool = typer.Option(
        False, "--force", help="Write into --out even if it is not a known prerender tree."
    ),
) -> None:
    """Capture GET routes to static HTML under public/build/prerender."""
    from fastplace.config import load_env
    from fastplace.prerender.capture import capture_all
    from fastplace.prerender.routes import resolve_prerender_routes
    from fastplace.prerender.writer import write_pages

    root = _project_root()
    load_env(root / ".env")

    try:
        app, app_module = _load_app(root, app_ref)
    except Exception as exc:  # noqa: BLE001 — any boot failure is a clean exit 1
        console.print(f"[red]could not import the app:[/] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        routes = resolve_prerender_routes(app_module, list(route or []), root=root)
    except ValueError as exc:
        console.print(f"[red]bad prerender route:[/] {exc}")
        raise typer.Exit(code=2) from exc

    if not routes:
        console.print("Nothing to prerender — no routes configured.")
        raise typer.Exit(code=0)

    try:
        pages = asyncio.run(capture_all(app, routes, timeout=timeout))
    except Exception as exc:  # noqa: BLE001 — any capture crash is a clean exit 1
        console.print(f"[red]prerender failed:[/] {exc}")
        raise typer.Exit(code=1) from exc

    out_dir = out if out is not None else root / "public" / "build" / "prerender"
    try:
        manifest = write_pages(pages, out_dir, force=force)
        manifest.write(out_dir)
    except ValueError as exc:
        console.print(f"[red]cannot write prerender output:[/] {exc}")
        raise typer.Exit(code=2) from exc

    from rich.table import Table

    table = Table(title="Prerendered pages")
    table.add_column("Route")
    table.add_column("Status")
    table.add_column("Bytes", justify="right")
    for page in pages:
        written = page.route in manifest.hashes
        table.add_row(
            page.route,
            str(page.status),
            str(len(page.body)) if written else "-",
        )
    console.print(table)
    skipped = len(manifest.skipped)
    summary = f"Prerendered {len(manifest.routes)} page(s) → {out_dir}"
    if skipped:
        summary += f" ({skipped} skipped)"
    console.print(summary)
