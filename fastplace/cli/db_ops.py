"""Database connection operations — roadmap data plane (db:* / migrate:*)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import typer

from fastplace.config import config, load_env

if TYPE_CHECKING:  # annotations stay lazy; runtime imports remain function-local
    from fastplace.cli._doctor import Check

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
            console.print(
                "[red]--params must be a JSON object[/] of named binds, e.g. '{\"id\": 3}'"
            )
            raise typer.Exit(code=1)
        binds = parsed

    family = _statement_family(sql)
    if family == "write" and not execute:
        console.print("[red]write statement refused[/] — pass [cyan]--execute[/] to run it")
        raise typer.Exit(code=1)
    if (
        family == "write"
        and str(config("APP_ENV", default="production")).lower() == "production"
        and not (force or typer.confirm("Run a write statement against the production database?"))
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


def _pg_target(url: str) -> tuple[str, str | None]:
    """(password-free URI, password) for pg_dump's libpq.

    The password rides in the child's PGPASSWORD environment instead of
    the URI: argv is readable by every local account (`ps`) for the whole
    dump; environment is not.
    """
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url, None
    scheme = scheme.split("+", 1)[0]
    parts = urlsplit(f"{scheme}://{rest}")
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"  # urlsplit strips IPv6 brackets
    netloc = host
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    if parts.username:
        netloc = f"{parts.username}@{netloc}"
    uri = urlunsplit((scheme, netloc, parts.path, parts.query, parts.fragment))
    return uri, parts.password or None


def _mysql_argv(url: str) -> list[str]:
    """mysqldump-compatible args from a SQLAlchemy URL.

    The password never appears here — it travels as MYSQL_PWD in the
    child's environment (argv is readable via `ps` for the whole dump).
    """
    parts = urlsplit(url)
    argv = ["--host", parts.hostname or "localhost"]
    if parts.port:
        argv += ["--port", str(parts.port)]
    if parts.username:
        argv += ["--user", parts.username]
    argv.append(parts.path.lstrip("/"))
    return argv


def _dir_size(root: Path) -> int:
    """Bytes under a directory tree (0 when absent — mongodump's --out dir)."""
    if not root.is_dir():
        return 0
    return sum(item.stat().st_size for item in root.rglob("*") if item.is_file())


def _run_dump(
    argv: list[str], *, stdout: IO[bytes] | None = None, env: dict[str, str] | None = None
) -> None:
    from fastplace.console import console

    # Secrets ride in the child's environment, merged over ours so the
    # child keeps PATH and locale; argv stays secret-free.
    child_env = {**os.environ, **env} if env else None
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=stdout is None,
            stdout=stdout,
            stderr=None if stdout is None else subprocess.PIPE,
            env=child_env,
        )
    except FileNotFoundError:
        console.print(f"[red]'{argv[0]}' not found — install the client or add it to PATH[/]")
        raise typer.Exit(code=127) from None
    if completed.returncode != 0:
        from rich.markup import escape

        stderr = completed.stderr or b""
        console.print(f"[red]'{argv[0]}' failed[/] — {escape(stderr.decode(errors='replace'))}")
        raise typer.Exit(code=1)


def _export_documents(backups: Path, url: str) -> Path:
    """Dump the Mongo database at ``url`` into a timestamped directory."""
    from fastplace.console import console

    dest = _backup_path(backups, "mongodb", "")
    # mongodump has no password environment variable — the URI (and
    # any credentials in it) must travel as argv, visible to local
    # accounts via ps for the lifetime of the dump. Say so instead
    # of pretending otherwise.
    if urlsplit(url).password:
        console.print(
            "[dim]note: mongodump receives the connection URI in its "
            "arguments — credentials are visible in the process list "
            "during the dump[/]"
        )
    _run_dump(["mongodump", f"--uri={url}", f"--out={dest}"])
    return dest


@db_ops_app.command("db:export")
def db_export() -> None:
    """Write a timestamped backup of every configured database to storage/backups/.

    The relational store rides DATABASE_URL as before; a configured
    MONGODB_URL adds a mongodump of the document store in the same run —
    an export that silently skips half the application's data is not a
    backup.
    """
    from rich.markup import escape

    from fastplace.console import console
    from fastplace.db import db

    load_env()
    backups = Path.cwd() / "storage" / "backups"
    backups.mkdir(parents=True, exist_ok=True)

    url = str(db.manager.config_for("default")["url"])
    family = _driver_family(url)
    exported: list[Path] = []

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
        exported.append(dest)
    elif family == "postgresql":
        dest = _backup_path(backups, "postgresql", "sql")
        uri, password = _pg_target(url)
        _run_dump(
            ["pg_dump", uri, "--file", str(dest)],
            env={"PGPASSWORD": password} if password else None,
        )
        exported.append(dest)
    elif family in ("mysql", "mariadb"):
        dest = _backup_path(backups, family, "sql")
        password = urlsplit(url).password
        with dest.open("wb") as sink:
            _run_dump(
                ["mysqldump", *_mysql_argv(url)],
                stdout=sink,
                env={"MYSQL_PWD": password} if password else None,
            )
        exported.append(dest)
    elif family != "mongodb":
        console.print(f"[red]no export strategy for driver '{escape(family)}'[/]")
        raise typer.Exit(code=1)

    # The document store is configured independently of DATABASE_URL —
    # export it whenever MONGODB_URL says one exists. A DATABASE_URL that
    # is itself a Mongo URL (single-store project) covers the legacy case.
    mongo_url = str(config("MONGODB_URL", default="") or "")
    if mongo_url:
        exported.append(_export_documents(backups, mongo_url))
    elif family == "mongodb":
        exported.append(_export_documents(backups, url))

    for dest in exported:
        size = dest.stat().st_size if dest.is_file() else _dir_size(dest)
        console.print(f"[green]exported[/] — {escape(str(dest))} ({size} bytes)")


#: (driver module, fastplace extra) per connection family. sqlite ships with
#: the dev dependency set — no extra to install, hence ``None``.
_DB_DRIVER_EXTRAS: dict[str, tuple[str, str] | None] = {
    "sqlite": None,
    "postgresql": ("asyncpg", "postgresql"),
    "postgres": ("asyncpg", "postgresql"),
    "mysql": ("asyncmy", "mysql"),
    "mariadb": ("asyncmy", "mysql"),
    "mongodb": ("pymongo", "mongodb"),
}


def _db_url_family() -> tuple[str, str]:
    """(family, raw DATABASE_URL) — family is "" when the URL is unset."""
    url = str(config("DATABASE_URL", default="") or "")
    if not url:
        return "", url
    return _driver_family(url), url


def _check_driver_url() -> Check:
    from fastplace.cli._doctor import Check

    family, url = _db_url_family()
    if not family:
        return Check(
            "driver_url",
            "fail",
            "DATABASE_URL is not set",
            "set DATABASE_URL in .env (sqlite+aiosqlite:///… for zero-config)",
        )
    if family not in _DB_DRIVER_EXTRAS:
        return Check(
            "driver_url",
            "fail",
            f"unknown scheme '{family}://' — expected sqlite, postgresql, mysql, or mongodb",
            f"check the DATABASE_URL scheme in .env: {url.partition('://')[0]}:// is not supported",
        )
    return Check("driver_url", "pass", family)


def _check_driver_extra() -> Check:
    import importlib.util

    from fastplace.cli._doctor import Check

    family, _ = _db_url_family()
    extra = _DB_DRIVER_EXTRAS.get(family or "sqlite")
    if extra is None:
        return Check("driver_extra", "pass", "sqlite — built in, no extra needed")
    module, extra_name = extra
    if importlib.util.find_spec(module) is None:
        return Check(
            "driver_extra",
            "fail",
            f"{family} URLs need the '{module}' driver, which is not installed",
            f'pip install "fastplace[{extra_name}]"',
        )
    return Check("driver_extra", "pass", f"{module} importable")


def _check_migrations(root: Path) -> Check:
    from fastplace.cli._doctor import Check

    versions = Path(root) / "database" / "migrations" / "versions"
    if not versions.is_dir():
        return Check(
            "migrations",
            "fail",
            "database/migrations/versions/ does not exist",
            "fastplace db:configure",
        )
    revisions = [p for p in versions.iterdir() if p.suffix == ".py" and p.name != "__init__.py"]
    if not revisions:
        return Check(
            "migrations",
            "warn",
            "scaffold present but no revisions yet",
            "fastplace make:migration <name>",
        )
    return Check("migrations", "pass", f"{len(revisions)} revision file(s)")


def _check_vector_capability(root: Path) -> Check:
    from fastplace.cli._doctor import Check
    from fastplace.cli.ai_ops import _vector_columns
    from fastplace.cli.inspect import collect_models

    family, _ = _db_url_family()
    declared: list[str] = []
    for model_cls in collect_models(Path(root)):  # type: ignore[arg-type]
        for column, _dims in _vector_columns(model_cls):
            declared.append(f"{model_cls.__name__}.{column}")
    if not declared:
        return Check("vector_capability", "pass", "no vector columns declared")
    if family != "postgresql":
        return Check(
            "vector_capability",
            "fail",
            f"vector columns {', '.join(declared)} need pgvector",
            "switch DATABASE_URL to postgresql:// and run CREATE EXTENSION vector",
        )

    async def _probe() -> bool:
        from sqlalchemy import text

        from fastplace.db import db

        engine = db.manager.engine("default")
        try:
            async with engine.connect() as connection:
                row = await connection.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                )
                return row.scalar() is not None
        finally:
            await engine.dispose()

    try:
        present = asyncio.run(_probe())
    except Exception as exc:  # noqa: BLE001 — unreachability is itself the finding
        return Check(
            "vector_capability",
            "fail",
            f"cannot probe pg_extension: {type(exc).__name__}",
            "run fastplace db:health — the database must be reachable to verify pgvector",
        )
    if present:
        return Check("vector_capability", "pass", f"pgvector live for {', '.join(declared)}")
    return Check(
        "vector_capability",
        "fail",
        f"vector columns {', '.join(declared)} but extension 'vector' is not installed",
        "CREATE EXTENSION vector;",
    )


@db_ops_app.command("db:doctor")
def db_doctor() -> None:
    """DB stack diagnosis: driver coherence, extras, migrations, vector capability."""
    from fastplace.cli._doctor import run_checks
    from fastplace.cli.system import _project_root
    from fastplace.config import reset_config

    root = _project_root()
    load_env(root / ".env")
    # Bind the config registry to the invoked project — otherwise the
    # process singleton keeps whatever defaults an earlier command loaded,
    # and driver_url would PASS against a foreign project's DATABASE_URL.
    reset_config(root)

    # Named closures (not lambdas) so a raising check degrades to a readable
    # row name — run_checks' fallback derives it from __name__.
    def _migrations_check() -> Check:
        return _check_migrations(root)

    def _vector_check() -> Check:
        return _check_vector_capability(root)

    code = run_checks(
        "Database doctor",
        [
            _check_driver_url,
            _check_driver_extra,
            _migrations_check,
            _vector_check,
        ],
    )
    raise typer.Exit(code=code)
