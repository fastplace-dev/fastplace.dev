"""Cache maintenance commands — `fastplace cache:clear`, `cache:forget`."""

from __future__ import annotations

from typing import Any

import typer

from fastplace.console import console

cache_app = typer.Typer(help="Cache store maintenance.")


@cache_app.command("cache:clear")
def cache_clear() -> None:
    """Flush every key from the configured cache store."""

    async def _run() -> None:
        from fastplace.cache import cache

        await cache().flush()

    from fastplace.config import load_env

    load_env()
    import asyncio

    asyncio.run(_run())
    console.print("[green]Cache cleared.[/green]")


@cache_app.command("cache:forget")
def cache_forget(
    key: str = typer.Argument(help="The cache key to remove."),
) -> None:
    """Remove one key from the cache (idempotent — a missing key is not an error)."""

    async def _run() -> Any:
        from fastplace.cache import cache

        store = cache()
        existing = await store.get(key)
        await store.forget(key)  # every driver treats this as delete-if-present
        return existing

    from fastplace.config import load_env

    load_env()
    import asyncio

    existing = asyncio.run(_run())
    if existing is None:
        console.print(f"[yellow]Cache key '{key}' not found — nothing to forget.[/yellow]")
        return
    console.print(f"[green]Forgot cache key '{key}'.[/green]")
