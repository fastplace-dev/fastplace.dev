"""Database lifecycle commands — migrations, seeders, reset (blueprint §10)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import typer

from fastplace.console import console

database_app = typer.Typer(help="Database: migrations, seeders, schema state.")

_MIGRATIONS_NOT_CONFIGURED = typer.style(
    "Migrations are not configured — run ", fg=typer.colors.YELLOW
) + typer.style("fastplace db:configure", fg=typer.colors.CYAN, bold=True)

#: driver → the env block `db:configure <driver>` writes. mongodb is the
#: document adapter: it carries MONGODB_URL, not the relational DATABASE_* keys.
_DRIVER_ENV: dict[str, dict[str, str]] = {
    "sqlite": {
        "DATABASE_DRIVER": "sqlite",
        "DATABASE_URL": "sqlite+aiosqlite:///./database.sqlite3",
    },
    "postgresql": {
        "DATABASE_DRIVER": "postgresql",
        "DATABASE_URL": "postgresql+asyncpg://user:password@localhost/fastplace",
    },
    "mysql": {
        "DATABASE_DRIVER": "mysql",
        "DATABASE_URL": "mysql+asyncmy://user:password@localhost/fastplace",
    },
    "mongodb": {"MONGODB_URL": "mongodb://localhost:27017/fastplace"},
}


def _project_root() -> Path:
    return Path.cwd()


def _manager():
    from fastplace.orm.migrations import MigrationsManager

    return MigrationsManager(_project_root())


def _upsert_env_lines(text: str, values: dict[str, str]) -> str:
    """Replace existing ``KEY=`` lines in place (commented or not); append the rest.

    Keys already present — even commented out — keep their position so a
    hand-organized .env never gets duplicate blocks. Every occurrence of a
    key is rewritten and later duplicates collapse into the first:
    python-dotenv resolves duplicate keys last-wins, so a surviving stale
    second line would silently undo the switch.
    """
    seen: set[str] = set()
    kept: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*#?\s*([A-Z0-9_]+)\s*=", line)
        key = match.group(1) if match is not None else None
        if key is None or key not in values:
            kept.append(line)
            continue
        if key in seen:
            continue  # duplicate line of an already-rewritten key — drop it
        kept.append(f"{key}={values[key]}")
        seen.add(key)
    remaining = [key for key in values if key not in seen]
    if remaining:
        if kept and kept[-1].strip():
            kept.append("")
        kept.extend(f"{key}={values[key]}" for key in remaining)
    return "\n".join(kept) + ("\n" if kept else "")


def _ensure_example_keys(example: Path, values: dict[str, str]) -> None:
    """Keep .env.example covering every key db:configure can write."""
    text = example.read_text() if example.exists() else "# Fastplace environment template.\n"
    missing = {
        key: value
        for key, value in values.items()
        if not re.search(rf"^\s*#?\s*{key}\s*=", text, re.MULTILINE)
    }
    if missing:
        additions = "\n".join(f"# {key}={value}" for key, value in missing.items())
        text = (
            text.rstrip("\n")
            + f"\n\n# Switchable via `fastplace db:configure <driver>`\n{additions}\n"
        )
        example.write_text(text)


#: Every value db:configure ever writes — switching between these is the
#: command's normal job; anything else in .env is operator-authored config.
_KNOWN_PLACEHOLDER_VALUES = frozenset(
    value for block in _DRIVER_ENV.values() for value in block.values()
)


def _custom_active_values(text: str, values: dict[str, str]) -> dict[str, str]:
    """Active KEY=value lines this write would overwrite with something that
    is neither the value already there nor a known placeholder."""
    custom: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if match is None:
            continue  # commented or plain text — only active assignments count
        key, current = match.group(1), match.group(2)
        if key not in values:
            continue
        if current == values[key] or current in _KNOWN_PLACEHOLDER_VALUES:
            continue
        custom[key] = current
    return custom


def _write_driver_env(driver: str, root: Path, *, force: bool = False) -> dict[str, str]:
    """Write the driver's DATABASE_* block into .env (and keep .env.example synced).

    Refuses to overwrite operator-authored values (real connection strings)
    unless ``force`` — clobbering them was silent and unrecoverable (.env is
    never in version control). With ``force`` the old file survives as
    ``.env.bak`` so the switch is always reversible by hand.
    """
    values = _DRIVER_ENV[driver]
    env = root / ".env"
    if not env.exists():
        example = root / ".env.example"
        env.write_text(example.read_text() if example.exists() else "")
    else:
        custom = _custom_active_values(env.read_text(), values)
        if custom and not force:
            console.print(
                "[red]refusing to overwrite[/] existing value(s): " + ", ".join(sorted(custom))
            )
            console.print(
                "these look hand-configured — re-run with [cyan]--force[/] to replace "
                "them (the previous .env is kept as .env.bak)"
            )
            raise typer.Exit(code=1)
        if env.read_text().strip():
            shutil.copyfile(env, env.parent / (env.name + ".bak"))
    env.write_text(_upsert_env_lines(env.read_text(), values))
    # The example documents the whole switchable surface, not just this driver.
    # Shared DATABASE_* keys show the recommended (postgresql) values.
    every_key = {
        **_DRIVER_ENV["sqlite"],
        **_DRIVER_ENV["mysql"],
        **_DRIVER_ENV["postgresql"],
        **_DRIVER_ENV["mongodb"],
    }
    _ensure_example_keys(root / ".env.example", every_key)
    return values


@database_app.command("db:configure")
def db_configure(
    driver: str = typer.Argument(
        None,
        help="Write this driver's DATABASE_* block into .env: sqlite, postgresql, mysql, mongodb.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Replace hand-configured values too (previous .env kept as .env.bak).",
    ),
) -> None:
    """Scaffold the Alembic env; with a driver, also switch .env to it."""
    root = _project_root()
    if driver is not None and driver not in _DRIVER_ENV:
        console.print(
            f"[red]unknown driver[/] {driver!r} — choose one of: " + ", ".join(sorted(_DRIVER_ENV))
        )
        raise typer.Exit(code=1)

    created = _manager().scaffold()
    if created:
        for path in created:
            console.print(f"[green]created[/] {path.relative_to(root)}")
    else:
        console.print("[dim]migrations already configured[/]")

    if driver is not None:
        written = _write_driver_env(driver, root, force=force)
        for key in written:
            console.print(f"[green]set[/] {key} in .env ({driver})")


@database_app.command("make:migration")
def make_migration(
    name: str = typer.Argument(..., help="Migration message, e.g. create_posts_table"),
    empty: bool = typer.Option(
        False, "--empty", "-e", help="Skip autogenerate — an empty skeleton to hand-write."
    ),
) -> None:
    """Autogenerate a migration from declared model changes (--empty to hand-write)."""
    manager = _manager()
    if not manager.configured:
        console.print(_MIGRATIONS_NOT_CONFIGURED)
        raise typer.Exit(code=1)
    revision = manager.make_empty(name) if empty else manager.make(name)
    if revision is not None:
        console.print(f"[green]created[/] {revision.relative_to(_project_root())}")


@database_app.command("migrate")
def migrate(
    pretend: bool = typer.Option(
        False, "--pretend", "-p", help="Print the SQL that would run instead of running it."
    ),
) -> None:
    """Run pending migrations (alembic upgrade head; --pretend previews the SQL)."""
    from alembic.util import CommandError

    manager = _manager()
    if not manager.configured:
        console.print(_MIGRATIONS_NOT_CONFIGURED)
        raise typer.Exit(code=1)
    if pretend:
        try:
            script = manager.pretend()
        except CommandError as exc:
            # Offline mode cannot reflect tables, so histories with batch
            # ALTERs (SQLite) without copy_from cannot be rendered as SQL.
            console.print(f"[red]cannot render this migration history offline:[/] {exc}")
            raise typer.Exit(code=1) from exc
        if not script:
            console.print("[dim]nothing to migrate[/]")
            return
        # Offline mode: raw SQL, so no Rich markup and no re-wrapped lines.
        console.print("[dim]pretend — SQL that would run (nothing was applied):[/]")
        console.print(script, markup=False, soft_wrap=True)
        return
    manager.upgrade()
    console.print("[green]migrated[/] database to head")


@database_app.command("migrate:rollback")
def migrate_rollback(
    steps: int = typer.Option(
        None,
        "--steps",
        "-s",
        help="Revert N individual revisions (default: the whole last batch).",
    ),
) -> None:
    """Revert the latest migration batch, or --steps individual revisions."""
    manager = _manager()
    if not manager.configured:
        console.print(_MIGRATIONS_NOT_CONFIGURED)
        raise typer.Exit(code=1)
    reverted = manager._last_batch_size() if steps is None else steps
    manager.downgrade(steps)
    console.print(f"[green]rolled back[/] {reverted} migration(s)")


@database_app.command("migrate:status")
def migrate_status() -> None:
    """Show applied/pending migrations."""
    _print_migration_status()


@database_app.command("migration:status", hidden=True)
def migration_status() -> None:
    """Show applied/pending migrations (legacy alias of migrate:status)."""
    _print_migration_status()


def _print_migration_status() -> None:
    console.print(_manager().status())


@database_app.command("migrate:reset")
def migrate_reset() -> None:
    """Revert every migration (downgrade to base — no rebuild, no seed)."""
    manager = _manager()
    if not manager.configured:
        console.print(_MIGRATIONS_NOT_CONFIGURED)
        raise typer.Exit(code=1)
    manager.downgrade("base")
    console.print("[green]reset[/] — all migrations reverted (database is at base)")


@database_app.command("db:seed")
def db_seed(
    seeder: str = typer.Option(
        None,
        "--seeder",
        help="Run one seeder: exact stem (user_seeder) or without the _seeder suffix (user).",
    ),
) -> None:
    """Run all seeders in database/seeders/ — or a single one with --seeder."""
    from fastplace.orm.migrations import run_seeders

    try:
        ran = run_seeders(_project_root(), seeder=seeder)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
    if ran:
        for name in ran:
            console.print(f"[green]seeded[/] {name}")
    else:
        console.print("[dim]no seeders found in database/seeders/[/]")


@database_app.command("db:reset")
def db_reset() -> None:
    """Drop everything through migrations, rebuild, and re-seed."""
    manager = _manager()
    if not manager.configured:
        console.print(_MIGRATIONS_NOT_CONFIGURED)
        raise typer.Exit(code=1)
    manager.reset()

    from fastplace.orm.migrations import run_seeders

    ran = run_seeders(_project_root())
    console.print("[green]reset[/] schema rebuilt to head")
    for name in ran:
        console.print(f"[green]seeded[/] {name}")


@database_app.command("db:wipe")
def db_wipe(
    force: bool = typer.Option(
        False, "--force", help="Skip the production confirmation prompt."
    ),
) -> None:
    """Drop every table and view, migration state included (no rebuild, no seed)."""
    from fastplace.config import config, load_env

    load_env()
    # A destructive command guards unless the environment explicitly says so.
    if str(config("APP_ENV", default="production")).lower() == "production" and not (
        force or typer.confirm("Wipe the production database? This drops every table.")
    ):
        console.print("[red]aborted[/] — the database was left untouched")
        raise typer.Exit(code=1)

    manager = _manager()
    if not manager.configured:
        console.print(_MIGRATIONS_NOT_CONFIGURED)
        raise typer.Exit(code=1)
    manager.downgrade("base")
    _drop_everything_remaining(manager)
    console.print("[green]wiped[/] — every table and view dropped (migration state included)")


def _drop_everything_remaining(manager) -> None:
    """Drop what downgrade-to-base left behind: every table and every view.

    Spec outcome for a wipe is an empty database, but downgrade only reaches
    tables the migration history knows about — out-of-history tables, stray
    views, and the (now empty) alembic_version bookkeeping would survive.
    Names come from live catalog reflection, so no hardcoded list can go
    stale. Full Table reflection is deliberately avoided: a stray table can
    carry a FOREIGN KEY pointing at a table that no longer exists, which
    makes ``MetaData.reflect()`` raise NoSuchTableError instead of dropping.
    """
    import asyncio

    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import create_async_engine

    def _drop_sync(connection) -> None:
        inspector = inspect(connection)
        preparer = connection.dialect.identifier_preparer
        # Views first: they may read from tables about to be dropped.
        for view in inspector.get_view_names():
            connection.exec_driver_sql(f"DROP VIEW IF EXISTS {preparer.quote(view)}")
        # Disarm FK checks so drop order cannot matter — including the
        # dangling references strays tend to carry.
        _disable_fk_checks(connection)
        for name in inspector.get_table_names():
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS {preparer.quote(name)}")

    async def _drop() -> None:
        engine = create_async_engine(manager._database_url())
        try:
            async with engine.begin() as conn:
                await conn.run_sync(_drop_sync)
        finally:
            await engine.dispose()

    asyncio.run(_drop())


def _disable_fk_checks(connection) -> None:
    """Best-effort per-dialect FK disarm for the wipe's unordered drops."""
    dialect = connection.dialect.name
    if dialect == "postgresql":
        connection.exec_driver_sql("SET session_replication_role = replica")
    elif dialect in ("mysql", "mariadb"):
        connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 0")
    # sqlite: FK enforcement is off unless the app opted in per connection,
    # and the pragma is a no-op inside a transaction anyway.


@database_app.command("session:gc")
def session_gc(
    lifetime: int = typer.Option(
        0, "--lifetime", help="Idle seconds before sweeping (0 = SESSION_LIFETIME)."
    ),
) -> None:
    """Sweep expired server-side sessions (spec §4.1)."""
    import asyncio

    from fastplace.config import load_env

    load_env()

    async def _run() -> int:
        # Imported here so tests can monkeypatch the factory symbol.
        from fastplace.http.session import session_store

        window: int | None = lifetime or None  # 0 -> the store's configured window
        return await session_store().gc(window)

    removed = asyncio.run(_run())
    console.print(f"[green]Swept {removed} expired session(s).[/green]")
