"""Auth maintenance commands — token housekeeping and session revocation."""

from __future__ import annotations

import typer

from fastplace.console import console

auth_app = typer.Typer(help="Auth maintenance commands.")


@auth_app.command("auth:clear-resets")
def clear_resets() -> None:
    """Purge expired password-reset tokens."""

    async def _run() -> int:
        from fastplace.auth.passwords import token_store

        return await token_store().purge_expired()

    from fastplace.config import load_env

    load_env()
    import asyncio

    removed = asyncio.run(_run())
    console.print(f"[green]Purged {removed} expired password-reset token(s).[/green]")


@auth_app.command("auth:prune-tokens")
def prune_tokens() -> None:
    """Purge expired personal access tokens."""

    async def _run() -> int:
        from fastplace.auth.tokens import pat_store

        return await pat_store().prune_expired()

    from fastplace.config import load_env

    load_env()
    import asyncio

    removed = asyncio.run(_run())
    console.print(f"[green]Purged {removed} expired personal access token(s).[/green]")


@auth_app.command("auth:logout-everywhere")
def logout_everywhere(
    user: int = typer.Argument(..., help="User id whose sessions are destroyed."),
) -> None:
    """Destroy every active session for a user (incident response, spec #48)."""

    async def _run() -> int:
        # Imported here so tests can monkeypatch the factory symbol — the
        # same seam session:gc uses.
        from fastplace.http.session import session_store

        return await session_store().destroy_for_user(user)

    from fastplace.config import load_env

    load_env()
    import asyncio

    removed = asyncio.run(_run())
    if removed:
        console.print(f"[green]Destroyed {removed} session(s) for user {user}.[/green]")
    else:
        console.print(f"[dim]User {user} has no active sessions.[/dim]")
