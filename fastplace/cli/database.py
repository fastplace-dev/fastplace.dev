"""Database lifecycle commands — migrations, seeders, reset (blueprint §10)."""

from __future__ import annotations

from pathlib import Path

import typer

from fastplace.console import console

database_app = typer.Typer(help="Database: migrations, seeders, schema state.")

_MIGRATIONS_NOT_CONFIGURED = typer.style(
    "Migrations are not configured — run ", fg=typer.colors.YELLOW
) + typer.style("fastplace db:configure", fg=typer.colors.CYAN, bold=True)


def _project_root() -> Path:
    return Path.cwd()


def _manager():
    from fastplace.orm.migrations import MigrationsManager

    return MigrationsManager(_project_root())


@database_app.command("db:configure")
def db_configure() -> None:
    """Scaffold database/migrations/ with the framework-managed Alembic env."""
    created = _manager().scaffold()
    if created:
        for path in created:
            console.print(f"[green]created[/] {path.relative_to(_project_root())}")
    else:
        console.print("[dim]migrations already configured[/]")


@database_app.command("make:migration")
def make_migration(
    name: str = typer.Argument(..., help="Migration message, e.g. create_posts_table"),
) -> None:
    """Autogenerate a migration from declared model changes."""
    manager = _manager()
    if not manager.configured:
        console.print(_MIGRATIONS_NOT_CONFIGURED)
        raise typer.Exit(code=1)
    revision = manager.make(name)
    if revision is not None:
        console.print(f"[green]created[/] {revision.relative_to(_project_root())}")


@database_app.command("migrate")
def migrate() -> None:
    """Run pending migrations (alembic upgrade head)."""
    manager = _manager()
    if not manager.configured:
        console.print(_MIGRATIONS_NOT_CONFIGURED)
        raise typer.Exit(code=1)
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


@database_app.command("migration:status")
def migration_status() -> None:
    """Show applied/pending migrations."""
    console.print(_manager().status())


@database_app.command("db:seed")
def db_seed() -> None:
    """Run all seeders in database/seeders/ (module-level async run())."""
    from fastplace.orm.migrations import run_seeders

    ran = run_seeders(_project_root())
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
