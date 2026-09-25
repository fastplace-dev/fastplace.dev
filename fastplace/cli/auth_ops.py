"""App-plane auth commands: tokens, gates, sessions, 2FA, password resets."""

from __future__ import annotations

import typer

from fastplace.console import console

auth_ops_app = typer.Typer(help="Auth & security operations (tokens, gates, sessions, 2FA).")


@auth_ops_app.command("token:list")
def token_list(
    user: int | None = typer.Option(None, "--user", help="Only tokens owned by this user id."),
) -> None:
    """List personal access tokens, newest first."""
    import asyncio

    from fastplace.config import load_env

    load_env()

    async def _run() -> list:
        from fastplace.auth.tokens import pat_store

        if user is not None:
            return await pat_store().list_for_user(user)
        return await pat_store().list_all()

    rows = asyncio.run(_run())
    if not rows:
        scope = f" for user {user}" if user is not None else ""
        console.print(f"[dim]no personal access tokens{scope}[/]")
        return

    from rich.table import Table

    table = Table(title="Personal access tokens")
    for column in ("id", "user", "name", "abilities", "last used", "expires"):
        table.add_column(column)
    for row in rows:
        abilities = ",".join(row.abilities or [])
        table.add_row(
            str(row.id),
            str(row.user_id),
            row.name,
            abilities or "—",
            str(row.last_used_at) if row.last_used_at else "—",
            str(row.expires_at) if row.expires_at else "never",
        )
    console.print(table)
