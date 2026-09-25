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


@maintenance_app.command("maintenance:status")
def maintenance_status(
    exit_code: bool = typer.Option(
        False,
        "--exit-code",
        help="Exit 0 up / 1 down / 2 state-file unreadable-or-corrupt. Without this flag always exit 0.",
    ),
) -> None:
    """Read-only 503-gate report: up, down+settings, or the two fault states."""
    import datetime
    import json

    from fastplace.config import load_env
    from fastplace.http.maintenance import MAINTENANCE_FILE

    root = _project_root()
    load_env()

    # is_down() collapses the fault states (OSError reads as "up", non-dict
    # JSON as the empty fail-closed state), so status reads the file itself
    # to split all four: absent / parsed / corrupt / unreadable.
    state_path = root / MAINTENANCE_FILE
    if not state_path.exists():
        console.print("status: [green]UP[/] (no state file)")
        raise typer.Exit(code=0)

    try:
        raw = state_path.read_text(encoding="utf-8")
    except OSError:
        console.print(
            "[red]state file present but UNREADABLE[/] — the gate silently "
            "re-opens. Fix permissions on "
            f"{MAINTENANCE_FILE} or run [bold]fastplace up[/] to reset."
        )
        raise typer.Exit(code=2 if exit_code else 0) from None

    state = None
    try:
        state = json.loads(raw)
    except ValueError:
        pass
    if not isinstance(state, dict):  # matches is_down: non-object JSON is fail-closed
        console.print(
            "status: [yellow]DOWN — state unreadable (corrupt JSON), "
            "fail-closed: everyone gets 503.[/]\n"
            f"Fix {MAINTENANCE_FILE} or run [bold]fastplace up[/] to reset."
        )
        raise typer.Exit(code=2 if exit_code else 0)

    try:
        mtime = state_path.stat().st_mtime
        since = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        since = "unknown"

    bits = [
        f"status: [yellow]DOWN[/] — down-since: {since} "
        "(state-file-last-written; `down` rewrites update it)",
    ]
    # Settings print verbatim, but the bypass secret shows presence only —
    # status output lands in deploy logs, never the secret value itself.
    bits.append(f"retry={state.get('retry')} refresh={state.get('refresh')}")
    bits.append(f"secret={'set' if state.get('secret') else 'none'}")
    console.print("\n".join(bits))
    raise typer.Exit(code=1 if exit_code else 0)
