"""Database connection operations — roadmap data plane (db:* / migrate:*)."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time

import typer

from fastplace.config import config, load_env

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


#: Statement families — the first word decides; everything not on this list
#: is treated as a write.
_READ_KEYWORDS = {"SELECT", "WITH", "EXPLAIN", "SHOW", "PRAGMA", "TABLE"}

#: String-literal runs — single- or double-quoted, doubled-quote escapes
#: allowed. Removed before the semicolon scan so a literal like ``'a;b'``
#: is not mistaken for a statement separator.
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")


def _statement_family(sql: str) -> str:
    """'read' for the SELECT-family, 'write' for everything else.

    Semicolons inside string literals are fine; any statement-separating
    semicolon (non-space content after the first one) makes the whole
    invocation 'write' so a batch cannot smuggle DML past the gate.
    """
    stripped = sql.strip().lstrip("(")
    body = _STRING_LITERAL.sub("''", stripped)
    if ";" in body:
        head, _, tail = body.partition(";")
        if head.strip() and tail.strip():
            return "write"
    first = body.split(None, 1)[0] if body else ""
    return "read" if first.upper() in _READ_KEYWORDS else "write"


@db_ops_app.command("db:query")
def db_query(
    sql: str = typer.Argument("-", help="SQL statement, or - to read from stdin."),
    params: str = typer.Option(None, "--params", help="JSON object of named bind parameters."),
    execute: bool = typer.Option(False, "--execute", help="Allow write statements (DML/DDL)."),
    force: bool = typer.Option(False, "--force", help="Skip the production confirmation prompt."),
) -> None:
    """Run one SQL statement through the app's own async engine."""
    from rich.markup import escape
    from rich.table import Table

    from fastplace.console import console
    from fastplace.db import db

    load_env()
    if sql == "-":
        sql = sys.stdin.read()

    binds: dict | None = None
    if params is not None:
        try:
            parsed = json.loads(params)
        except ValueError as exc:
            console.print(f"[red]invalid --params JSON[/] — {escape(str(exc))}")
            raise typer.Exit(code=1) from None
        if not isinstance(parsed, dict):
            console.print("[red]--params must be a JSON object[/] of named binds, e.g. '{\"id\": 3}'")
            raise typer.Exit(code=1)
        binds = parsed

    family = _statement_family(sql)
    if family == "write" and not execute:
        console.print("[red]write statement refused[/] — pass [cyan]--execute[/] to run it")
        raise typer.Exit(code=1)
    if family == "write" and str(config("APP_ENV", default="production")).lower() == "production" and not (
        force or typer.confirm("Run a write statement against the production database?")
    ):
        console.print("[red]aborted[/] — the database was left untouched")
        raise typer.Exit(code=1)

    async def _run() -> list[dict]:
        return await db.raw(sql, binds)

    rows = asyncio.run(_run())
    if not rows:
        console.print("[dim]0 rows[/]")
        return
    table = Table()
    for column in rows[0]:
        table.add_column(str(column))
    for row in rows:
        table.add_row(*[_render_cell(value) for value in row.values()])
    console.print(table)


def _render_cell(value: object) -> str:
    from rich.markup import escape

    if isinstance(value, str):
        return escape(value)
    try:
        return escape(json.dumps(value))
    except (TypeError, ValueError):
        return escape(repr(value))
