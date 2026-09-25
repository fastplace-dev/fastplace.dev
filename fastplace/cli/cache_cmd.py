"""Cache maintenance commands — `fastplace cache:clear`, `cache:forget`, `throttle:clear`."""

from __future__ import annotations

from typing import Any, cast

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


@cache_app.command("cache:gc")
def cache_gc(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would be purged, delete nothing."),
) -> None:
    """Sweep expired rows from the database cache driver (the whole cache table)."""
    import asyncio

    from rich.markup import escape

    from fastplace.config import config, load_env

    load_env()
    driver = str(config("CACHE_DRIVER", default="memory"))
    if driver != "database":
        console.print(
            f"[dim]driver '{escape(driver)}' expires keys natively — nothing to sweep[/]"
        )
        return

    from fastplace.cache import DatabaseCache, cache, reset_cache

    reset_cache()
    # The driver gate above pins the factory's product to DatabaseCache; the
    # CacheStore Protocol deliberately omits the driver-specific purge_expired.
    store = cast(DatabaseCache, cache())

    async def _run() -> int:
        if dry_run:
            import time as _time

            from sqlalchemy import select

            from fastplace.cache import _cache_table
            from fastplace.db import db

            # A first-run project has no cache table yet; the non-dry path
            # self-heals via purge_expired()'s own _ensure_table(), so the
            # count SELECT must not skip it either (idempotent via _ensured).
            await store._ensure_table()
            engine = db.manager.engine("default")
            async with engine.connect() as connection:
                rows = await connection.execute(
                    select(_cache_table.c.key).where(
                        _cache_table.c.expires_at.is_not(None),
                        _cache_table.c.expires_at <= int(_time.time()),
                    )
                )
                return len(rows.fetchall())
        return await store.purge_expired()

    purged = asyncio.run(_run())
    if dry_run:
        console.print(f"Would purge {purged} expired row(s).")
    else:
        console.print(f"Purged {purged} expired row(s).")


@cache_app.command("cache:get")
def cache_get(key: str = typer.Argument(help="The cache key to read.")) -> None:
    """Read one cache key and show its value and remaining TTL.

    A stored JSON ``null`` is reported as not-found — the drivers cannot
    distinguish it from an absent key, so this command keeps that parity.
    """
    import asyncio
    import json

    from rich.markup import escape

    from fastplace.config import load_env

    load_env()
    # No reset_cache() here: in a one-shot CLI the singleton is always built
    # from the current config, and resetting would discard the in-process
    # memory store's contents — exactly what this command exists to read.
    from fastplace.cache import cache

    store = cache()

    async def _run() -> tuple[Any, float | None]:
        return await store.get(key), await store.ttl(key)

    value, ttl = asyncio.run(_run())
    if value is None:
        console.print(f"[yellow]cache key '{escape(key)}' not found[/]")
        raise typer.Exit(code=1)
    try:
        rendered = json.dumps(value)
    except (TypeError, ValueError):
        rendered = repr(value)
    # Drivers report remaining seconds as a float (monotonic-deadline math);
    # a countdown renders as whole seconds.
    suffix = "no expiry" if ttl is None else f"expires in {int(ttl)}s"
    console.print(f"{escape(rendered)} [dim]({suffix})[/]")


@cache_app.command("cache:put")
def cache_put(
    key: str = typer.Argument(..., help="Cache key to write."),
    value: str = typer.Argument(..., help="Value — parsed as JSON first, raw string otherwise."),
    ttl: int = typer.Option(None, "--ttl", help="Seconds until expiry; omit for no expiry."),
) -> None:
    """Write one cache key. Omit --ttl to keep it forever."""
    import asyncio
    import json

    from rich.markup import escape

    from fastplace.config import load_env

    load_env()
    # No reset_cache() here — same reasoning as cache:get: the singleton must
    # bind to the current config while keeping any in-process store contents.
    try:
        parsed = json.loads(value)
    except ValueError:
        parsed = value

    from fastplace.cache import cache
    from fastplace.errors import ConfigurationError

    try:
        store = cache()

        async def _run() -> None:
            await store.put(key, parsed, ttl=ttl)

        asyncio.run(_run())
    except ValueError as exc:  # _validate_ttl contract — ttl <= 0 or non-numeric
        console.print(f"[red]invalid ttl[/] — {escape(str(exc))}")
        raise typer.Exit(code=1) from None
    except ConfigurationError as exc:
        console.print(f"[red]cache misconfigured[/] — {escape(str(exc))}")
        raise typer.Exit(code=1) from None

    suffix = "" if ttl is None else f" (ttl {ttl}s)"
    console.print(f"[green]Set cache key '{escape(key)}'[/]{suffix}")


@cache_app.command("throttle:status")
def throttle_status(
    key: str = typer.Argument(..., help="Rate-limiter key to inspect."),
    max_attempts: int = typer.Option(60, "--max", help="Attempt ceiling the middleware enforces."),
) -> None:
    """Show attempts and block state for one throttle key. Reads only."""
    import asyncio

    from rich.markup import escape

    from fastplace.config import load_env

    load_env()

    async def _run() -> tuple[int, int]:
        # Imported here so tests can monkeypatch the limiter symbol —
        # the same seam throttle:clear uses; the limiter applies the
        # ratelimit: key prefix itself, so reads match middleware writes.
        from fastplace.ratelimit import RateLimiter

        limiter = RateLimiter()
        attempts = await limiter.attempts(key)
        free_in = await limiter.available_in(key)
        return attempts, free_in

    attempts, free_in = asyncio.run(_run())
    console.print(f"{escape(key)}: [bold]{attempts}/{max_attempts}[/] attempts")
    if attempts >= max_attempts:
        console.print(f"[yellow]blocked — free in {free_in}s[/]")
    else:
        console.print("[green]not blocked[/]")
