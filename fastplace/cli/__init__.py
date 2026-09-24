"""The `fastplace` CLI — Typer + Rich developer interface (blueprint §10)."""

from __future__ import annotations

import typer

from fastplace import __version__
from fastplace.cli.dev import run_app, serve_app
from fastplace.cli.shell_cmd import shell_app

app = typer.Typer(
    name="fastplace",
    help="Fastplace — full-stack, AI-native web framework.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command("about")
def about() -> None:
    """Show framework + application environment information."""
    import platform
    import sys

    from fastplace.config import config, load_env

    load_env()

    from rich.panel import Panel
    from rich.table import Table

    from fastplace.console import console

    table = Table(show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column(style="bold")
    table.add_row("Fastplace", __version__)
    table.add_row("Python", f"{platform.python_version()} ({sys.executable})")
    table.add_row("Application", str(config("APP_NAME", default="Fastplace")))
    table.add_row("Environment", str(config("APP_ENV", default="production")))
    table.add_row("Database", str(config("DATABASE_DRIVER", default="sqlite")))
    console.print(Panel(table, title="[fastplace]Fastplace[/]", expand=False))


# Server runtimes: `fastplace run dev`, `fastplace serve`
app.add_typer(run_app, name="run")
app.add_typer(serve_app, name="")


# Later phases register their command groups here (migrations, generators, …).
def _register_phase_commands() -> None:  # pragma: no cover - wiring only
    try:
        from fastplace.cli.auth import auth_app

        app.add_typer(auth_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.database import database_app

        app.add_typer(database_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.generators import generators_app

        app.add_typer(generators_app, name="")

        # Attaches make:auth onto the generators group (the command
        # decorator runs at import time).
        import fastplace.cli.auth_scaffold  # noqa: F401
    except ImportError:
        pass
    try:
        from fastplace.cli.lint import lint_app

        app.add_typer(lint_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.queue import queue_app

        app.add_typer(queue_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.system import system_app

        app.add_typer(system_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.inspect import inspect_app

        app.add_typer(inspect_app, name="")
    except ImportError:
        pass


_register_phase_commands()
app.add_typer(shell_app, name="")


if __name__ == "__main__":  # pragma: no cover
    app()
