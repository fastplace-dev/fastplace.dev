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


@auth_ops_app.command("gate:check")
def gate_check(
    user_id: int,
    ability: str,
    args: list[str] = typer.Option(
        [], "--arg", help="Gate argument as JSON (repeatable); forwarded in order."
    ),
) -> None:
    """Check one ability for one user.

    JSON args arrive as plain dicts/lists/strings — abilities expecting model
    instances receive them as parsed JSON.
    """
    import asyncio
    import json

    from fastplace.config import load_env
    from fastplace.errors import ConfigurationError

    load_env()
    from fastplace.cli.system import _project_root

    root = _project_root()

    parsed: list = []
    for raw in args:
        try:
            parsed.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            console.print(f"[red]invalid --arg JSON:[/] {raw} ({exc})")
            raise typer.Exit(code=1) from exc

    async def _run() -> dict:
        from fastplace.authz.loader import import_gates

        import_gates(root)
        from fastplace.cli.provisioning import _accounts_repository

        try:
            repository = _accounts_repository()
        except ImportError:
            console.print("[red]no accounts module found[/] — run [bold]make:auth[/] first")
            raise typer.Exit(code=1) from None
        user = await repository.find_by_id(user_id)
        if user is None:
            console.print(f"[red]no user with id {user_id}[/]")
            raise typer.Exit(code=1)
        from fastplace.authz.gate import gate

        return await gate.inspect(user, ability, *parsed)

    try:
        verdict = asyncio.run(_run())
    except ConfigurationError:
        console.print(f"[red]ability '{ability}' is not registered[/]")
        raise typer.Exit(code=1) from None
    mark = "[green]allow[/]" if verdict["allowed"] else "[red]deny[/]"
    console.print(f"{mark}  {verdict['ability']}  [dim](source: {verdict['source']})[/]")
    if verdict.get("message"):
        console.print(f"  {verdict['message']}")
    if not verdict["allowed"]:
        raise typer.Exit(code=1)
