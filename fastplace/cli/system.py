"""System commands: command inventory, environment, log tailing."""

from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path

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


#: Backfill shown before the live follow starts (and the default cap of the
#: single-pass `--lines` mode's bigger sibling — `tail`'s familiar 20).
_DEFAULT_BACKFILL = 20
_FOLLOW_POLL_SECONDS = 0.5


def _project_root() -> Path:
    """The cwd when it is a Fastplace project; a friendly exit otherwise."""
    root = Path.cwd()
    if not (root / "asgi.py").is_file():
        console.print(
            "[red]not inside a Fastplace project[/] — run this from a project root "
            "(the directory containing asgi.py)."
        )
        raise typer.Exit(code=1)
    return root


def _newest_log(logs_dir: Path) -> Path | None:
    """The most recently modified ``*.log`` in storage/logs, if any."""
    return max(logs_dir.glob("*.log"), key=lambda path: path.stat().st_mtime, default=None)


def _level_matches(line: str, level: str | None) -> bool:
    """Case-insensitive level filter — the level name appears in the line."""
    return level is None or level.upper() in line.upper()


@system_app.command("log:tail")
def log_tail(
    level: str | None = typer.Option(
        None, "--level", help="Show only lines carrying this level name (e.g. ERROR, INFO)."
    ),
    file: Path | None = typer.Option(
        None, "--file", help="Tail this file instead of the newest storage/logs/*.log."
    ),
    lines: int | None = typer.Option(
        None,
        "--lines",
        "-n",
        help="Print the last N matching lines and exit — no live follow.",
    ),
) -> None:
    """Tail the newest storage/logs/*.log live (--level filters; --lines N prints and exits)."""
    root = _project_root()
    path = file if file is not None else _newest_log(root / "storage" / "logs")
    if path is None or not path.is_file():
        console.print(
            "[red]no log file found[/] — expected storage/logs/*.log (or pass [cyan]--file[/])"
        )
        raise typer.Exit(code=1)

    backfill = _DEFAULT_BACKFILL if lines is None else max(lines, 0)
    # Stream the backfill through a bounded window — a log is the canonical
    # large growing dataset, so never materialize the whole file in memory.
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        recent: deque[str] = deque(maxlen=backfill)
        for line in handle:
            if _level_matches(line, level):
                recent.append(line.rstrip("\n"))
    for line in recent:
        # Raw log lines: markup off so bracketed content prints verbatim.
        console.print(line, markup=False, highlight=False)
    if lines is not None:
        return

    # Live follow: block on the file, printing matching lines as they land.
    # An empty read is EOF-for-now — the writer may append more — so poll
    # rather than exit; Ctrl+C ends the follow cleanly (the backfill above
    # already printed).
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, os.SEEK_END)
            while True:
                raw = handle.readline()
                if not raw:
                    time.sleep(_FOLLOW_POLL_SECONDS)
                    continue
                if _level_matches(raw, level):
                    console.print(raw.rstrip("\n"), markup=False, highlight=False)
    except KeyboardInterrupt:  # pragma: no cover - interactive Ctrl+C
        pass
