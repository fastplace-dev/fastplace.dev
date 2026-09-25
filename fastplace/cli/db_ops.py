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


def _describe_op(op: tuple) -> tuple[str, str, str]:
    """One human line per alembic autogen diff entry.

    Tuple shapes (alembic 1.20, verified against ``operations/ops.py``
    ``to_diff_tuple``): ``("add_table", Table)``, ``("add_column", schema,
    table_name, column)``, and ``("modify_*", schema, table_name,
    column_name, context, old, new)``.
    """
    kind = op[0]
    if kind in ("add_table", "remove_table"):
        return kind.replace("_", " "), op[1].name, "-"
    if kind in ("add_column", "remove_column"):
        return kind.replace("_", " "), str(op[2]), op[3].name
    if kind.startswith("modify_"):
        return kind.replace("_", " "), str(op[2]), f"{op[3]}: {op[5]} → {op[6]}"
    return kind, "-", "-"


#: Framework bookkeeping tables that live beside the schema but are not part
#: of it — the same set the scaffolded Alembic env excludes (env.py.tpl), so
#: migrate:check reports exactly the drift `make:migration` would generate.
#: (alembic_version is also auto-excluded by Alembic itself; kept for parity.)
_FRAMEWORK_TABLES = frozenset({"fastplace_migrations", "alembic_version"})


def _include_object(obj, name, type_, reflected, compare_to):  # noqa: ARG001 — alembic hook
    if type_ == "table" and name in _FRAMEWORK_TABLES:
        return False
    return True


@db_ops_app.command("migrate:check")
def migrate_check() -> None:
    """Compare model metadata against the live schema without writing a revision."""
    from pathlib import Path

    from rich.markup import escape
    from rich.table import Table

    from fastplace.cli.inspect import collect_models
    from fastplace.console import console
    from fastplace.db import db
    from fastplace.orm import Model
    from fastplace.orm.migrations import MigrationsManager

    load_env()

    migrations = MigrationsManager(Path.cwd())
    if not migrations.configured:
        console.print(
            "[red]migrations are not configured[/] — run [cyan]fastplace db:configure[/] first"
        )
        raise typer.Exit(code=1)

    collect_models(Path.cwd())

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    async def _run() -> list[tuple]:
        async with db.manager.engine("default").connect() as connection:
            return await connection.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(sync_conn, opts={"include_object": _include_object}),
                    Model.metadata,
                )
            )

    raw_diff = asyncio.run(_run())
    # Alter-column groups arrive as nested lists of diff tuples — flatten so
    # one table row == one diff entry and the count matches the table.
    diff = [entry for item in raw_diff for entry in (item if isinstance(item, list) else [item])]
    if not diff:
        console.print("[green]clean[/] — models match the live schema")
        return

    table = Table(title="Schema drift (models vs live database)")
    table.add_column("op", style="bold")
    table.add_column("table", style="cyan")
    table.add_column("detail")
    for op in diff:
        label, target, detail = _describe_op(op)
        table.add_row(label, escape(str(target)), escape(str(detail)))
    console.print(table)
    console.print(f"[red]{len(diff)} pending change(s)[/] — run [cyan]fastplace make:migration[/]")
    raise typer.Exit(code=1)
