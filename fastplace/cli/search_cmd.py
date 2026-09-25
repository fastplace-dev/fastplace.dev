"""Search operations — roadmap data plane (search:*)."""

from __future__ import annotations

from pathlib import Path

import typer

from fastplace.config import load_env
from fastplace.console import console

search_cmd_app = typer.Typer(help="Search operations.")


def _project_root() -> Path:
    """Nearest ancestor containing asgi.py, or a red exit (schedule.py guard)."""
    candidate = Path.cwd()
    for directory in (candidate, *candidate.parents):
        if (directory / "asgi.py").is_file():
            return directory
    console.print(
        "[red]not inside a Fastplace project[/] — run this from a project root "
        "(the directory containing asgi.py)."
    )
    raise typer.Exit(code=1)


@search_cmd_app.command("search:query")
def search_query(
    query: str = typer.Argument(..., help="Search terms."),
    model: str = typer.Option(None, "--model", help="Model class name to search within."),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum results."),
) -> None:
    """Full-text search through the registered search service."""
    import asyncio

    from rich.markup import escape
    from rich.table import Table

    from fastplace.cli.inspect import collect_models
    from fastplace.errors import SearchCapabilityMissing
    from fastplace.search import SearchNotSupported, get_search_service

    load_env()
    root = _project_root()

    if not model:
        console.print("[red]--model is required[/] — name the model to search")
        raise typer.Exit(code=1)

    models = collect_models(root)
    resolved = next((candidate for candidate in models if candidate.__name__ == model), None)
    if resolved is None:
        known = sorted(models, key=lambda candidate: candidate.__name__)
        console.print(f"[red]unknown model '{escape(model)}'[/]")
        console.print(f"known models: {', '.join(escape(c.__name__) for c in known) or 'none'}")
        raise typer.Exit(code=1)

    async def _run():
        return await get_search_service().search(query, model=resolved, limit=limit)

    try:
        results = asyncio.run(_run())
    except (SearchNotSupported, SearchCapabilityMissing) as exc:
        console.print(f"[red]search unavailable[/] — {escape(str(exc))}")
        console.print(
            "[dim]this driver has no full-text search — use PostgreSQL or register a "
            "custom service via register_search_service()[/]"
        )
        raise typer.Exit(code=1) from None

    table = Table(title=f"Results for '{escape(query)}'")
    table.add_column("pk", style="cyan")
    table.add_column("record")
    for instance in results:
        pk = getattr(instance, "pk", None) or getattr(instance, "id", "-")
        table.add_row(escape(str(pk)), escape(str(instance)))
    console.print(table)
