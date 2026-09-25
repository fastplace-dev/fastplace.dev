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


@mail_app.command("mail:preview")
def mail_preview(
    to: str = typer.Option("user@example.com", "--to", help="Recipient address."),
    subject: str = typer.Option("Test message", "--subject", help="Subject line."),
    text: str = typer.Option("Hello from fastplace.", "--text", help="Plain-text body."),
    html: str = typer.Option("", "--html", help="HTML body (rendered instead of --text)."),
) -> None:
    """Preview a message with the configured from-address defaults — no transport."""
    from rich.panel import Panel

    from fastplace.config import config, load_env

    load_env()
    from_address = str(config("MAIL_FROM_ADDRESS", default="fastplace@localhost"))
    from_name = str(config("MAIL_FROM_NAME", default="") or "") or None
    sender = f"{from_name} <{from_address}>" if from_name else from_address
    body = html or text
    console.print(
        Panel(
            f"[bold]from:[/]    {sender}\n[bold]to:[/]      {to}\n"
            f"[bold]subject:[/] {subject}\n\n{body}",
            title="Mail preview",
        )
    )


@mail_app.command("mail:clear")
def mail_clear(
    force: bool = typer.Option(False, "--force", help="Skip the production confirmation prompt."),
) -> None:
    """Drop every logged outbox message (storage/logs/mail.log)."""
    from fastplace.config import config, load_env

    load_env()
    if str(config("APP_ENV", default="production")).lower() == "production" and not (
        force or typer.confirm("Clear the mail outbox log? This drops every logged message.")
    ):
        console.print("[red]aborted[/] — the outbox log is untouched")
        raise typer.Exit(code=1)

    from fastplace.mail.transports import MAIL_LOG_PATH

    path = Path(MAIL_LOG_PATH)
    if not path.is_file():
        console.print(f"[dim]no mail log at {MAIL_LOG_PATH}[/]")
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        dropped = sum(1 for _ in handle)
    path.write_text("")
    console.print(f"dropped {dropped} logged message(s)")


@mail_app.command("mail:resend")
def mail_resend(
    line: int,
    force: bool = typer.Option(False, "--force", help="Skip the production confirmation prompt."),
) -> None:
    """Replay one logged message (a mail:outbox line number) through the active transport."""
    import asyncio
    import json

    from fastplace.config import config, load_env

    load_env()
    driver = str(config("MAIL_DRIVER", default="log") or "log").strip().lower()
    from fastplace.mail.transports import MAIL_LOG_PATH

    payload = None
    path = Path(MAIL_LOG_PATH)
    if path.is_file():
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for lineno, text in enumerate(handle, start=1):  # stream-count: bounded memory
                if lineno == line:
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        console.print(f"[red]line {line} is not valid JSON[/]")
                        raise typer.Exit(code=1) from None
                    break
    if payload is None:
        console.print(f"[red]no message at line {line}[/] — check mail:outbox")
        raise typer.Exit(code=1)

    recipient = str(payload.get("to", ""))
    subject = str(payload.get("subject", ""))
    if driver == "smtp":
        question = f"Resend '{subject}' to {recipient}? Real mail will be sent."
    else:
        question = f"Replay logged message {line} through the active transport?"
    if str(config("APP_ENV", default="production")).lower() == "production" and not (
        force or typer.confirm(question)
    ):
        console.print("[red]aborted[/] — nothing was sent")
        raise typer.Exit(code=1)

    async def _run() -> None:
        from fastplace.mail import Mail, message_from_dict

        await Mail.deliver(message_from_dict(payload))

    asyncio.run(_run())
    if driver == "log":
        console.print(f"replayed line {line} — appended a duplicate entry to the log")
    else:
        console.print(f"resent '{subject}' to {recipient} via {driver}")
