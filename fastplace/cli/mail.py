"""App-plane mail commands: outbox log, preview, clear, resend."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from fastplace.console import console

if TYPE_CHECKING:  # annotations stay lazy; runtime imports remain function-local
    from fastplace.cli._doctor import Check

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
    # The smtp driver sends real mail in every environment, so it gates the
    # confirmation alongside production — local must not mean silent sends.
    if (
        driver == "smtp" or str(config("APP_ENV", default="production")).lower() == "production"
    ) and not (force or typer.confirm(question)):
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


# ---------------------------------------------------------------------------
# mail:doctor — roadmap app plane doctor
# ---------------------------------------------------------------------------


def _mail_driver() -> str:
    from fastplace.config import config

    return str(config("MAIL_DRIVER", default="log") or "log").lower()


def _check_mail_driver() -> Check:
    from fastplace.cli._doctor import Check
    from fastplace.errors import ConfigurationError
    from fastplace.mail.transports import transport_for

    try:
        transport_for()
    except ConfigurationError as exc:
        return Check("driver", "fail", str(exc), "set MAIL_DRIVER to log, memory, or smtp in .env")
    return Check("driver", "pass", _mail_driver())


def _check_smtp_extra() -> Check:
    import importlib.util

    from fastplace.cli._doctor import Check

    if _mail_driver() != "smtp":
        return Check("smtp_extra", "pass", f"not needed (driver={_mail_driver()})")
    if importlib.util.find_spec("aiosmtplib") is None:
        return Check(
            "smtp_extra",
            "fail",
            "MAIL_DRIVER=smtp requires aiosmtplib, which is not installed",
            'pip install "fastplace[mail]"',
        )
    return Check("smtp_extra", "pass", "aiosmtplib importable")


def _check_smtp_config() -> Check:
    from fastplace.cli._doctor import Check
    from fastplace.config import config

    if _mail_driver() != "smtp":
        return Check("smtp_config", "pass", f"driver={_mail_driver()} — no smtp credentials needed")
    missing = [
        name
        for name in ("MAIL_HOST", "MAIL_USERNAME", "MAIL_PASSWORD")
        if not str(config(name, default="") or "")
    ]
    if missing:
        return Check(
            "smtp_config",
            "fail",
            f"missing: {', '.join(missing)}",
            "set MAIL_HOST / MAIL_USERNAME / MAIL_PASSWORD in .env",
        )
    # Masked summary: host and user are operational data; the password is
    # only ever reported as hidden — never rendered, even partially.
    host = str(config("MAIL_HOST", default=""))
    port = str(config("MAIL_PORT", default="25") or "25")
    user = str(config("MAIL_USERNAME", default=""))
    return Check("smtp_config", "pass", f"{host}:{port} {user} (password hidden)")


def _check_queued_worker() -> Check:
    from fastplace.cli._doctor import Check
    from fastplace.config import config

    queued = (
        _mail_driver() == "smtp" and str(config("QUEUE_DRIVER", default="memory")).lower() == "saq"
    )
    if not queued:
        return Check("queued_worker", "pass", "inline delivery (not queued)")
    return Check(
        "queued_worker",
        "warn",
        "smtp mail is queued — without a running worker nothing is delivered",
        "fastplace queue:work",
    )


def _check_mail_send_registration(root: Path) -> Check:
    from fastplace.cli._doctor import Check
    from fastplace.config import config

    queued = (
        _mail_driver() == "smtp" and str(config("QUEUE_DRIVER", default="memory")).lower() == "saq"
    )
    if not queued:
        return Check("mail_send", "pass", "not queued — no handler needed")
    from fastplace.queue import import_jobs, jobs

    import_jobs(root)
    if "mail_send" in jobs():
        return Check("mail_send", "pass", "handler registered")
    return Check(
        "mail_send",
        "warn",
        "no mail_send handler registered — queued mail would be dropped",
        'register a @Job(name="mail_send") handler in app/jobs/',
    )


def _check_connect(host: str, port: str, enabled: bool) -> Check:
    import asyncio

    from fastplace.cli._doctor import Check

    if not enabled:
        return Check("connect", "pass", "skipped (pass --connect for a live wire check)")
    if _mail_driver() != "smtp":
        return Check("connect", "pass", f"skipped (driver={_mail_driver()})")

    async def _wire() -> bytes:
        # Raw socket probe — dependency-free, so --connect works even before
        # the aiosmtplib extra is installed: banner in = wire live.
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port)), timeout=5.0
        )
        try:
            return await asyncio.wait_for(reader.readline(), timeout=5.0)
        finally:
            writer.close()
            await writer.wait_closed()

    try:
        asyncio.run(_wire())
    except Exception as exc:  # noqa: BLE001 — the refused wire is the finding
        return Check(
            "connect",
            "fail",
            f"{host}:{port} unreachable ({type(exc).__name__})",
            "check MAIL_HOST / MAIL_PORT and that the SMTP server accepts connections",
        )
    return Check("connect", "pass", f"{host}:{port} answered the SMTP banner")


@mail_app.command("mail:doctor")
def mail_doctor(
    connect: bool = typer.Option(False, "--connect", help="Open a live wire to the SMTP server."),
) -> None:
    """Mail stack diagnosis: driver, extra, masked config, queue readiness, wire."""
    from fastplace.cli._doctor import run_checks
    from fastplace.cli.system import _project_root
    from fastplace.config import config, load_env, reset_config

    root = _project_root()
    load_env(root / ".env")
    # Bind the config registry to the invoked project (doctor umbrella pattern).
    reset_config(root)

    host = str(config("MAIL_HOST", default="127.0.0.1"))
    port = str(config("MAIL_PORT", default="25") or "25")

    # Named closures (not lambdas) so a raising check degrades to a readable
    # row name — run_checks derives it from __name__.
    def _mail_send_check() -> Check:
        return _check_mail_send_registration(root)

    def _connect_check() -> Check:
        return _check_connect(host, port, connect)

    code = run_checks(
        "Mail doctor",
        [
            _check_mail_driver,
            _check_smtp_extra,
            _check_smtp_config,
            _check_queued_worker,
            _mail_send_check,
            _connect_check,
        ],
    )
    raise typer.Exit(code=code)
