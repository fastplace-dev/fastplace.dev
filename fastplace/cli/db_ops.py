"""Database connection operations — roadmap data plane (db:* / migrate:*)."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import IO
from urllib.parse import urlsplit

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

#: SQL comments — stripped after literals so a verb in a comment or a
#: quoted string can never force (or duck) the write classification.
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

#: Mutation verbs scanned anywhere in a read-classified statement. The
#: first keyword alone is not enough: a data-modifying CTE mutates rows
#: before its final SELECT, EXPLAIN ANALYZE *executes* the statement it
#: explains, and Postgres SELECT ... INTO creates a table. Any hit makes
#: the invocation 'write' — fail-safe, never the other way around.
_DML_VERBS = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|"
    r"GRANT|REVOKE|CALL|VACUUM|ATTACH|DETACH|ANALYZE|ANALYSE|INTO"
    r")\b",
    re.IGNORECASE,
)


def _statement_family(sql: str) -> str:
    """'read' for the SELECT-family, 'write' for everything else.

    Semicolons inside string literals are fine; any statement-separating
    semicolon (non-space content after the first one) makes the whole
    invocation 'write' so a batch cannot smuggle DML past the gate.
    String literals and comments are stripped, then the remainder is
    scanned for mutation verbs — a read-starting statement that mutates
    anywhere (CTE, EXPLAIN ANALYZE, SELECT INTO) is still 'write'.
    """
    stripped = sql.strip().lstrip("(")
    body = _STRING_LITERAL.sub("''", stripped)
    body = _BLOCK_COMMENT.sub(" ", body)
    body = _LINE_COMMENT.sub(" ", body)
    if ";" in body:
        head, _, tail = body.partition(";")
        if head.strip() and tail.strip():
            return "write"
    if _DML_VERBS.search(body):
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


def _truncate_statements(dialect_name: str, quoted_tables: list[str], cascade: bool) -> list[str]:
    """Dialect-specific statements that empty every listed table.

    Pure on purpose — the postgres/mysql shapes are unit-testable without
    any live service (roadmap B1 acceptance).
    """
    joined = ", ".join(quoted_tables)
    if dialect_name == "postgresql":
        return [f"TRUNCATE TABLE {joined} RESTART IDENTITY" + (" CASCADE" if cascade else "")]
    if dialect_name in ("mysql", "mariadb"):
        return (
            ["SET FOREIGN_KEY_CHECKS=0"]
            + [f"TRUNCATE TABLE {table}" for table in quoted_tables]
            + ["SET FOREIGN_KEY_CHECKS=1"]
        )
    # sqlite has no TRUNCATE — DELETE plus a best-effort sequence reset.
    statements = [f"DELETE FROM {table}" for table in quoted_tables]
    names = ", ".join(f"'{table.strip(chr(34))}'" for table in quoted_tables)
    statements.append(f"DELETE FROM sqlite_sequence WHERE name IN ({names})")
    return statements


@db_ops_app.command("db:truncate")
def db_truncate(
    tables: list[str] = typer.Argument(
        ..., help="Table names to truncate (validated against the live catalog)."
    ),
    cascade: bool = typer.Option(
        False, "--cascade", help="Also truncate tables with foreign keys pointing at these."
    ),
    force: bool = typer.Option(False, "--force", help="Skip the production confirmation prompt."),
) -> None:
    """Empty the named tables, validated against the live catalog first."""
    from rich.markup import escape
    from rich.table import Table

    from fastplace.console import console
    from fastplace.db import db

    load_env()

    async def _catalog_and_statements() -> tuple[list[str], list[str]]:
        from sqlalchemy import inspect as sa_inspect

        engine = db.manager.engine("default")
        async with engine.connect() as connection:

            def _inspect(sync_conn) -> tuple[list[str], list[str]]:
                inspector = sa_inspect(sync_conn)
                preparer = sync_conn.dialect.identifier_preparer
                quoted = [preparer.quote(name) for name in tables]
                return sorted(inspector.get_table_names()), _truncate_statements(
                    engine.dialect.name, quoted, cascade
                )

            return await connection.run_sync(_inspect)

    known, statements = asyncio.run(_catalog_and_statements())

    # Catalog validation comes first: an unknown table aborts before the guard
    # and before any statement executes, so a typo can never touch production.
    unknown = [table for table in tables if table not in known]
    if unknown:
        console.print(f"[red]unknown table '{escape(unknown[0])}'[/]")
        console.print(f"known tables: {', '.join(escape(k) for k in known)}")
        raise typer.Exit(code=1)

    if str(config("APP_ENV", default="production")).lower() == "production" and not (
        force or typer.confirm("Truncate tables in the production database?")
    ):
        console.print("[red]aborted[/] — the tables were left untouched")
        raise typer.Exit(code=1)

    async def _run() -> None:
        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError

        engine = db.manager.engine("default")
        async with engine.begin() as connection:
            for statement in statements:
                try:
                    await connection.execute(text(statement))
                except OperationalError:
                    # sqlite_sequence only exists once an AUTOINCREMENT table
                    # has fired — its reset is best-effort by design.
                    if "sqlite_sequence" in statement:
                        continue
                    raise

    asyncio.run(_run())

    table = Table(title="Truncated")
    table.add_column("table", style="cyan")
    for name in tables:
        table.add_row(escape(name))
    console.print(table)


def _unique_path(path: Path) -> Path:
    """First non-existent path: stem, stem-2, stem-3, … (same-second safe)."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _backup_path(backups_dir: Path, driver: str, ext: str) -> Path:
    """Timestamped, collision-safe destination: ``<driver>-YYYYMMDD-HHMMSS[.<ext>]``."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    name = f"{driver}-{stamp}" + (f".{ext}" if ext else "")
    return _unique_path(backups_dir / name)


def _driver_family(url: str) -> str:
    """Connection family from the URL scheme: sqlite, postgresql, mysql, …

    The scheme — already canonicalized by ``normalize_database_url`` — drives
    db:export's branching (not ``engine.dialect.name``): the dump clients must
    run without their python driver installed, and mongodb connections never
    get a SQLAlchemy dialect at all.
    """
    return url.partition("://")[0].split("+", 1)[0].lower()


def _base_uri(url: str) -> str:
    """Strip the python driver suffix so pg_dump's libpq accepts the URI."""
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url
    return f"{scheme.split('+', 1)[0]}://{rest}"


def _mysql_argv(url: str) -> list[str]:
    """mysqldump-compatible args from a SQLAlchemy URL.

    ``--password=`` only when the URL carries one — a bare ``-p`` would make
    mysqldump prompt interactively mid-backup.
    """
    parts = urlsplit(url)
    argv = ["--host", parts.hostname or "localhost"]
    if parts.port:
        argv += ["--port", str(parts.port)]
    if parts.username:
        argv += ["--user", parts.username]
    if parts.password:
        argv.append(f"--password={parts.password}")
    argv.append(parts.path.lstrip("/"))
    return argv


def _dir_size(root: Path) -> int:
    """Bytes under a directory tree (0 when absent — mongodump's --out dir)."""
    if not root.is_dir():
        return 0
    return sum(item.stat().st_size for item in root.rglob("*") if item.is_file())


def _run_dump(argv: list[str], *, stdout: IO[bytes] | None = None) -> None:
    from fastplace.console import console

    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=stdout is None,
            stdout=stdout,
            stderr=None if stdout is None else subprocess.PIPE,
        )
    except FileNotFoundError:
        console.print(f"[red]'{argv[0]}' not found — install the client or add it to PATH[/]")
        raise typer.Exit(code=127) from None
    if completed.returncode != 0:
        from rich.markup import escape

        stderr = completed.stderr or b""
        console.print(f"[red]'{argv[0]}' failed[/] — {escape(stderr.decode(errors='replace'))}")
        raise typer.Exit(code=1)


@db_ops_app.command("db:export")
def db_export() -> None:
    """Write a timestamped backup of the default database to storage/backups/."""
    from rich.markup import escape

    from fastplace.console import console
    from fastplace.db import db

    load_env()
    backups = Path.cwd() / "storage" / "backups"
    backups.mkdir(parents=True, exist_ok=True)

    url = str(db.manager.config_for("default")["url"])
    family = _driver_family(url)

    if family == "sqlite":
        dest = _backup_path(backups, "sqlite", "sqlite3")
        engine = db.manager.engine("default")

        async def _vacuum() -> None:
            # VACUUM cannot run inside a transaction; the aiosqlite dialect
            # opens no driver-level BEGIN ahead of a non-DML statement, so
            # this parameterized exec (no path spliced into the SQL text)
            # is the one proven path. Disposal keeps pooled connections from
            # pinning the source file after the backup exists.
            async with engine.connect() as connection:
                await connection.exec_driver_sql("VACUUM INTO ?", (str(dest),))
            await engine.dispose()

        asyncio.run(_vacuum())
    elif family == "postgresql":
        dest = _backup_path(backups, "postgresql", "sql")
        _run_dump(["pg_dump", _base_uri(url), "--file", str(dest)])
    elif family in ("mysql", "mariadb"):
        dest = _backup_path(backups, family, "sql")
        with dest.open("wb") as sink:
            _run_dump(["mysqldump", *_mysql_argv(url)], stdout=sink)
    elif family == "mongodb":
        dest = _backup_path(backups, "mongodb", "")
        _run_dump(["mongodump", f"--uri={url}", f"--out={dest}"])
    else:
        console.print(f"[red]no export strategy for driver '{escape(family)}'[/]")
        raise typer.Exit(code=1)

    size = dest.stat().st_size if dest.is_file() else _dir_size(dest)
    console.print(f"[green]exported[/] — {escape(str(dest))} ({size} bytes)")
