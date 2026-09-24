"""Maintenance mode commands — `fastplace down` / `fastplace up` (spec #61)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from fastplace.console import console

maintenance_app = typer.Typer(help="Application maintenance mode.")


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


@maintenance_app.command("down")
def down(
    retry: int | None = typer.Option(
        None, "--retry", help="Retry-After seconds sent with every 503 response."
    ),
    secret: str | None = typer.Option(
        None,
        "--secret",
        help="Bypass value — requests carrying ?secret=<value> (or the cookie) skip the 503.",
    ),
    refresh: int | None = typer.Option(
        None, "--refresh", help="Seconds before the 503 page auto-rechecks."
    ),
    force: bool = typer.Option(False, "--force", help="Skip the production confirmation prompt."),
) -> None:
    """Put the application into maintenance mode (every request gets a 503)."""
    import json

    from fastplace.config import config, load_env
    from fastplace.http.maintenance import MAINTENANCE_FILE

    root = _project_root()
    load_env()

    # A destructive command guards unless the environment explicitly says so.
    if str(config("APP_ENV", default="production")).lower() == "production" and not (
        force or typer.confirm("Bring the production application down for maintenance?")
    ):
        console.print("[red]aborted[/] — the application was left up")
        raise typer.Exit(code=1)

    # Only the options the operator passed land in the state — an absent
    # key means "not set" to the middleware, not an invented default.
    state: dict[str, Any] = {}
    if retry is not None:
        state["retry"] = retry
    if secret is not None:
        state["secret"] = secret
    if refresh is not None:
        state["refresh"] = refresh

    path = root / MAINTENANCE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state) + "\n")
    console.print("[green]down[/] — the application is in maintenance mode (503)")
    if secret is not None:
        console.print(f"[dim]bypass with[/] ?secret={secret}")


@maintenance_app.command("up")
def up() -> None:
    """Bring the application back from maintenance mode."""
    from fastplace.http.maintenance import MAINTENANCE_FILE

    path = _project_root() / MAINTENANCE_FILE
    if not path.exists():
        console.print("[dim]already up[/] — the application is not in maintenance mode")
        return
    path.unlink()
    console.print("[green]up[/] — the application left maintenance mode")
