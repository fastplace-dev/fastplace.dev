"""App-plane mail commands: outbox log, preview, clear, resend."""

from __future__ import annotations

from pathlib import Path

import typer

from fastplace.console import console

mail_app = typer.Typer(help="Mail operations (outbox log, preview, replay).")


@mail_app.command("mail:outbox")
def mail_outbox(
    to: str = typer.Option("", "--to", help="Substring filter on recipient."),
    subject: str = typer.Option("", "--subject", help="Substring filter on subject."),
    lines: int = typer.Option(50, "--lines", help="Show only the last N matches."),
    as_json: bool = typer.Option(False, "--json", help="Print a parsed JSON array."),
) -> None:
    """List logged mail messages — bounded window, true file line numbers."""
    import json
    from collections import deque

    from fastplace.config import load_env

    load_env()
    from fastplace.mail.transports import MAIL_LOG_PATH

    path = Path(MAIL_LOG_PATH)
    if not path.is_file():
        console.print(f"[dim]no mail log at {MAIL_LOG_PATH}[/]")
        return

    recent: deque[tuple[int, dict]] = deque(maxlen=max(lines, 0))
    skipped = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for lineno, line in enumerate(handle, start=1):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if to and to not in str(parsed.get("to", "")):
                continue
            if subject and subject not in str(parsed.get("subject", "")):
                continue
            recent.append((lineno, parsed))

    if skipped:
        console.print(f"[dim]skipped {skipped} malformed line(s)[/]")
    if not recent:
        console.print("[dim]no matching messages[/]")
        return
    if as_json:
        console.print_json(json.dumps([{"line": n, **msg} for n, msg in recent]))
        return

    from rich.table import Table

    table = Table(title="Mail outbox")
    for column in ("#", "to", "from", "subject"):
        table.add_column(column)
    for n, msg in recent:
        table.add_row(
            str(n),
            str(msg.get("to", "—")),
            str(msg.get("from_address", "—")),
            str(msg.get("subject", "—")),
        )
    console.print(table)
