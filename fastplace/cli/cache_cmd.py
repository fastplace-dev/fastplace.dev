"""Cache maintenance commands — `fastplace cache:clear`, `cache:forget`, `throttle:clear`."""

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


@cache_app.command("throttle:clear")
def throttle_clear(
    key: str = typer.Argument(help="The throttle counter key to reset."),
) -> None:
    """Reset one rate-limit counter — un-block a throttled client (spec #49)."""

    async def _run() -> None:
        # Imported here so tests can monkeypatch the limiter symbol; the
        # store binds to the configured cache exactly as the middleware's
        # limiter does.
        from fastplace.ratelimit import RateLimiter

        await RateLimiter().clear(key)

    from fastplace.config import load_env

    load_env()
    import asyncio

    asyncio.run(_run())
    console.print(f"[green]Cleared throttle counter '{key}'.[/green]")


@cache_app.command("cache:status")
def cache_status() -> None:
    """Report the active cache driver and probe that it answers."""
    import asyncio

    from rich.markup import escape
    from rich.table import Table

    from fastplace.config import config, load_env

    load_env()
    driver = str(config("CACHE_DRIVER", default="memory"))
    table = Table(title="Cache")
    table.add_column("setting", style="dim")
    table.add_column("value")
    table.add_row("driver", driver)
    table.add_row("prefix", str(config("CACHE_PREFIX", default="fastplace")))
    table.add_row("ttl", str(config("CACHE_TTL", default="3600")))

    async def _probe() -> str:
        if driver == "memory":
            from fastplace.cache import cache, reset_cache

            # Construct from the CURRENT config: a stale cached store would
            # both skip the production memory refusal and misreport the probe.
            reset_cache()
            cache()
            return "[dim]in-process store — always reachable[/]"
        if driver == "database":
            from fastplace.db import db

            engine = db.manager.engine("default")
            async with engine.connect() as connection:
                from sqlalchemy import text

                await connection.execute(text("SELECT 1"))
            await engine.dispose()
            return "[green]ok[/] — SELECT 1 answered"
        if driver == "redis":
            import redis.asyncio as aioredis

            client = aioredis.from_url(str(config("REDIS_URL", default="redis://localhost:6379/0")))
            try:
                await client.ping()
            finally:
                await client.aclose()
            return "[green]ok[/] — PING answered"
        raise RuntimeError(f"unknown CACHE_DRIVER '{driver}'")

    from fastplace.errors import ConfigurationError

    try:
        finding = asyncio.run(_probe())
    except ConfigurationError as exc:
        console.print(table)
        console.print(f"[red]cache misconfigured[/] — {escape(str(exc))}")
        raise typer.Exit(code=1) from None
    except Exception as exc:  # noqa: BLE001 — probe failure is the finding
        detail = str(exc)
        if type(exc).__name__ == "ModuleNotFoundError" and "redis" in detail:
            detail += " — install fastplace[redis]"
        console.print(table)
        console.print(f"[red]unreachable[/] — {escape(detail)}")
        raise typer.Exit(code=1) from None

    console.print(table)
    console.print(finding)
