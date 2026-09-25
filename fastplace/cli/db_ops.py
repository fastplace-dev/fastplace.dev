"""Database connection operations — roadmap data plane (db:* / migrate:*)."""

from __future__ import annotations

import asyncio
import time

import typer

from fastplace.config import load_env

db_ops_app = typer.Typer(help="Database connection operations.")


@db_ops_app.command("db:health")
def db_health() -> None:
    """Probe every configured connection and read replica with SELECT 1."""
    from rich.table import Table
    from sqlalchemy import text

    from fastplace.console import console
    from fastplace.db import db

    load_env()

    async def _run() -> bool:
        table = Table(title="Database connections")
        table.add_column("connection", style="bold cyan", no_wrap=True)
        table.add_column("driver")
        table.add_column("pool", justify="right")
        table.add_column("latency", justify="right")
        table.add_column("status")
        healthy = True
        engines: list[object] = []
        for name in sorted(db.manager.connections):
            cfg = db.manager.config_for(name)
            pool = cfg.get("pool_size")
            targets = [(name, db.manager.engine(name))]
            engines.append(targets[0][1])
            for index, replica in enumerate(db.manager.replica_engines(name), start=1):
                targets.append((f"{name} replica {index}", replica))
                engines.append(replica)
            for label, engine in targets:
                started = time.perf_counter()
                try:
                    async with engine.connect() as connection:
                        await connection.execute(text("SELECT 1"))
                    latency = (time.perf_counter() - started) * 1000
                    table.add_row(
                        label,
                        engine.dialect.name,
                        "-" if pool is None else str(pool),
                        f"{latency:.1f}ms",
                        "[green]ok[/]",
                    )
                except Exception as exc:  # noqa: BLE001 — any probe failure is a finding
                    from rich.markup import escape

                    healthy = False
                    table.add_row(
                        label,
                        engine.dialect.name,
                        "-" if pool is None else str(pool),
                        "-",
                        f"[red]unreachable: {escape(str(exc))}[/]",
                    )
        for probed in engines:
            with_suppressed = getattr(probed, "dispose", None)
            if with_suppressed is not None:
                try:
                    await with_suppressed()
                except Exception:  # noqa: BLE001 — dispose is best-effort
                    pass
        console.print(table)
        return healthy

    if not asyncio.run(_run()):
        raise typer.Exit(code=1)
