"""Database inspection & ops commands — db:show, db:table, db:cli, db:documents.

Thin printers over ``fastplace.db_inspection`` (the queue_failures split:
the CLI owns options, Rich output, and exit codes; the framework module
owns the SQLAlchemy/Mongo round-trips).
"""

from __future__ import annotations

import asyncio
import subprocess

import typer

from fastplace.config import config, load_env

db_inspect_app = typer.Typer(help="Live database inspection and native shell.")


@db_inspect_app.command("db:show")
def db_show(
    counts: bool = typer.Option(
        False, "--counts", help="Add a per-table row count (SELECT COUNT(*))."
    ),
) -> None:
    """Live overview: driver, database, pool, tables (--counts adds row counts)."""
    load_env()
    from rich.table import Table

    from fastplace.console import console
    from fastplace.db_inspection import database_overview

    overview = asyncio.run(database_overview(include_counts=counts))

    # Plain soft-wrapped lines, not a Rich table: the database name is often
    # an absolute path longer than the console width, and a table cell would
    # crop it to an ellipsis.
    pool = f"{overview.pool_size} connection(s)" if overview.pool_size is not None else "-"
    for label, value in (
        ("driver", overview.driver),
        ("database", overview.database or "(in-memory)"),
        ("pool", pool),
    ):
        console.print(f"[dim]{label:<9}[/] [bold]{value}[/]", soft_wrap=True)

    if counts:
        rows = Table(box=None, header_style="bold")
        rows.add_column("table", style="bold")
        rows.add_column("rows", justify="right")
        for table in overview.tables:
            rows.add_row(table.name, str(table.row_count))
        console.print(rows)
    else:
        names = ", ".join(table.name for table in overview.tables)
        console.print(f"[dim]tables[/] {names or '(none)'}")


@db_inspect_app.command("db:table")
def db_table(
    name: str = typer.Argument(..., help="Table to describe: columns, types, keys, indexes."),
) -> None:
    """Describe one table: columns, types, nullability, keys, and indexes."""
    load_env()
    from rich.table import Table

    from fastplace.console import console
    from fastplace.db_inspection import describe_table

    detail = asyncio.run(describe_table(name))
    if detail is None:
        console.print(f"[red]no table named[/] {name!r}")
        raise typer.Exit(code=1)

    columns = Table(box=None, header_style="bold")
    columns.add_column("column", style="bold", no_wrap=True)
    columns.add_column("type")
    columns.add_column("nullable")
    columns.add_column("key")
    for column in detail.columns:
        key = "primary" if column.primary_key else ""
        if column.foreign_keys:
            refs = ", ".join(f"-> {target}" for target in column.foreign_keys)
            key = f"{key} {refs}".strip()
        columns.add_row(column.name, column.type, "yes" if column.nullable else "no", key or "-")
    console.print(columns)

    if detail.indexes:
        indexes = Table(box=None, header_style="bold")
        indexes.add_column("index", style="bold", no_wrap=True)
        indexes.add_column("columns")
        indexes.add_column("unique")
        for index in detail.indexes:
            indexes.add_row(index.name, ", ".join(index.columns), "yes" if index.unique else "no")
        console.print(indexes)
    else:
        console.print("[dim]no indexes[/]")


@db_inspect_app.command("db:cli")
def db_cli() -> None:
    """Open the database's native interactive shell (sqlite3 / psql / mysql)."""
    load_env()
    from fastplace.console import console
    from fastplace.db_inspection import native_shell_argv

    try:
        argv = native_shell_argv()
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    console.print(f"[dim]$[/] {' '.join(argv)}")
    # os.execvp semantics via subprocess: the shell inherits this terminal
    # and its exit code becomes ours. A missing client (psql not installed,
    # say) is a friendly 127, not a traceback.
    try:
        completed = subprocess.run(argv, check=False)
    except FileNotFoundError:
        console.print(f"[red]'{argv[0]}' not found[/] — install the client or add it to PATH")
        raise typer.Exit(code=127) from None
    raise typer.Exit(code=completed.returncode)


@db_inspect_app.command("db:documents")
def db_documents() -> None:
    """Document-adapter status: Mongo collections and estimated counts."""
    load_env()
    from rich.table import Table

    from fastplace.console import console
    from fastplace.db_inspection import documents_status

    if not config("MONGODB_URL", default=None):
        console.print("[dim]documents adapter disabled — set MONGODB_URL in .env to enable it[/]")
        return

    stats = asyncio.run(documents_status())
    if not stats:
        console.print("[dim]no collections[/]")
        return

    table = Table(box=None, header_style="bold")
    table.add_column("collection", style="bold", no_wrap=True)
    table.add_column("documents", justify="right")
    for stat in stats:
        table.add_row(stat.name, str(stat.documents))
    console.print(table)
