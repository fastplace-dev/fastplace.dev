"""The `fastplace` CLI — Typer + Rich developer interface (blueprint §10)."""

from __future__ import annotations

from pathlib import Path

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
        from fastplace.cli.cache_cmd import cache_app

        app.add_typer(cache_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.database import database_app

        app.add_typer(database_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.db_inspect import db_inspect_app

        app.add_typer(db_inspect_app, name="")
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
        from fastplace.cli.keys import keys_app

        app.add_typer(keys_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.lint import lint_app

        app.add_typer(lint_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.maintenance import maintenance_app

        app.add_typer(maintenance_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.provisioning import provisioning_app

        app.add_typer(provisioning_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.queue import queue_app

        app.add_typer(queue_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.schedule import schedule_app

        app.add_typer(schedule_app, name="")
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
    # --- roadmap: app plane (auth / mail / ai) ---
    try:
        from fastplace.cli.auth_ops import auth_ops_app

        app.add_typer(auth_ops_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.mail import mail_app

        app.add_typer(mail_app, name="")
    except ImportError:
        pass
    try:
        from fastplace.cli.ai_ops import ai_ops_app

        app.add_typer(ai_ops_app, name="")
    except ImportError:
        pass


_register_phase_commands()
app.add_typer(shell_app, name="")


def load_app_commands(app: typer.Typer, root: Path) -> list[str]:
    """Mount project CLI commands from ``<root>/app/commands/*_command.py``.

    Each module must expose a module-level ``command_app: typer.Typer``; its
    commands attach directly onto the root CLI (spec #56). An absent
    directory — the framework repo itself ships no ``app/commands/`` — is a
    clean no-op, and a malformed module is skipped with a warning so one bad
    file can never take the whole CLI down.
    """
    import importlib
    import sys

    from fastplace.console import console

    commands_dir = root / "app" / "commands"
    if not commands_dir.is_dir():
        return []

    # The modules import as ``app.commands.<stem>``, so the project root must
    # be importable — a console-script run does not put the cwd on sys.path.
    # Stays inserted: mounted command callbacks may import more project
    # modules when invoked.
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    mounted: list[str] = []
    for path in sorted(commands_dir.glob("*_command.py")):
        try:
            module = importlib.import_module(f"app.commands.{path.stem}")
            command_app = getattr(module, "command_app", None)
            if not isinstance(command_app, typer.Typer):
                console.print(
                    f"[yellow]warning[/] app/commands/{path.name} defines no "
                    "command_app Typer — skipped"
                )
                continue
            app.add_typer(command_app, name="")
            for cmd in command_app.registered_commands:
                if cmd.callback is None:  # pragma: no cover - Typer always sets one
                    continue
                mounted.append(cmd.name or cmd.callback.__name__.replace("_", "-"))
        except Exception as exc:  # noqa: BLE001 — one bad module never kills the CLI
            console.print(f"[yellow]warning[/] could not load app/commands/{path.name}: {exc}")
    return mounted


# Project-defined commands mount at CLI bootstrap. Outside a project (the
# framework repo itself) the directory is absent and this is a clean no-op.
load_app_commands(app, Path.cwd())


if __name__ == "__main__":  # pragma: no cover
    app()
