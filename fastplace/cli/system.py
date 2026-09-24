"""System commands: command inventory, environment, log tailing."""

from __future__ import annotations

import typer

from fastplace.console import console

system_app = typer.Typer(help="Framework system information.")


def _walk(app: typer.Typer, prefix: str = "") -> list[tuple[str, str]]:
    """Recursively collect (command name, help) from a Typer app tree."""
    out: list[tuple[str, str]] = []
    for cmd in app.registered_commands:
        if cmd.callback is None:  # pragma: no cover - Typer always sets one
            continue
        name = cmd.name or cmd.callback.__name__.replace("_", "-")
        doc = cmd.help or cmd.callback.__doc__ or ""
        help_text = doc.strip().splitlines()[0] if doc.strip() else ""
        out.append((prefix + name, help_text))
    for group in app.registered_groups:
        if group.typer_instance is None:  # pragma: no cover - add_typer sets it
            continue
        group_name = group.name or ""
        sub_prefix = f"{prefix}{group_name} " if group_name else prefix
        out.extend(_walk(group.typer_instance, sub_prefix))
    return out


def iter_command_names(app: typer.Typer) -> list[tuple[str, str]]:
    """All (command, help) pairs, flattened and sorted; group prefix stays (``run dev``), colon names stay (``make:model``)."""
    return sorted(set(_walk(app)))


@system_app.command("list")
def list_commands(
    raw: bool = typer.Option(False, "--raw", help="Print plain command names only."),
) -> None:
    """List every registered command, grouped by namespace."""
    from fastplace.cli import app as root  # local import avoids cycle

    items = iter_command_names(root)
    if raw:
        for name, _ in items:
            console.print(name)
        return
    from rich.table import Table

    table = Table(title="Fastplace commands", show_lines=False)
    table.add_column("namespace", style="dim")
    table.add_column("command", style="bold")
    table.add_column("description")
    for name, help_text in items:
        namespace = name.split(":", 1)[0] if ":" in name else "available"
        command = name.split(":", 1)[1] if ":" in name else name
        table.add_row(namespace, command, help_text)
    console.print(table)


@system_app.command("env")
def env() -> None:
    """Display the current framework environment."""
    from fastplace.config import config, load_env

    load_env()
    console.print(f"APP_ENV={config('APP_ENV', default='production')}")
