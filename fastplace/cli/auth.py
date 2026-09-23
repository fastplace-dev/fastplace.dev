"""Auth maintenance commands — expired-token housekeeping (spec §4.10)."""

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
