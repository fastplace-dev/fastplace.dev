"""Mail transports — log, memory (tests), and smtp (optional aiosmtplib)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastplace.config import config
from fastplace.errors import ConfigurationError
from fastplace.mail.message import MailMessage

#: Log-driver destination (cwd-relative — `fastplace run dev` writes repo-root).
MAIL_LOG_PATH = Path("storage/logs/mail.log")

# memory driver state — tests read this between requests.
_outbox: list[MailMessage] = []


def mail_outbox() -> list[MailMessage]:
    """Messages delivered by the memory driver, oldest first."""
    return list(_outbox)


def clear_mail_outbox() -> None:
    """Drop every captured message — test isolation."""
    _outbox.clear()


async def send_via_log(message: MailMessage) -> None:
    """Append one JSON line per message to storage/logs/mail.log."""
    MAIL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "subject": message.subject,
            "text": message.text,
            "to": message.to,
            "html": message.html,
            "from_address": message.from_address,
        },
        ensure_ascii=False,
    )
    with MAIL_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


async def send_via_memory(message: MailMessage) -> None:
    """Capture the message in-process (tests and local dev)."""
    _outbox.append(message)


async def send_via_smtp(message: MailMessage) -> None:
    """Deliver through aiosmtplib (`pip install 'fastplace[mail]'`)."""
    try:
        import aiosmtplib
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ConfigurationError(
            "MAIL_DRIVER=smtp requires aiosmtplib — pip install 'fastplace[mail]'"
        ) from exc

    from email.message import EmailMessage

    letter = EmailMessage()
    sender = message.from_address or str(config("MAIL_FROM_ADDRESS", default="fastplace@localhost"))
    letter["From"] = sender
    letter["To"] = message.to
    letter["Subject"] = message.subject
    letter.set_content(message.text)
    if message.html:
        letter.add_alternative(message.html, subtype="html")

    encryption = str(config("MAIL_ENCRYPTION", default="none") or "none")
    await aiosmtplib.send(
        letter,
        hostname=str(config("MAIL_HOST", default="127.0.0.1")),
        port=int(config("MAIL_PORT", default=25) or 25),
        username=config("MAIL_USERNAME") or None,
        password=config("MAIL_PASSWORD") or None,
        use_tls=encryption == "tls",
        start_tls=encryption == "starttls",
    )


_TRANSPORTS = {
    "log": send_via_log,
    "memory": send_via_memory,
    "smtp": send_via_smtp,
}


def transport_for(driver: str | None = None) -> Any:
    """Resolve the transport callable for a MAIL_DRIVER (default: config)."""
    resolved = driver or str(config("MAIL_DRIVER", default="log") or "log")
    transport = _TRANSPORTS.get(resolved)
    if transport is None:
        raise ConfigurationError(f"unknown MAIL_DRIVER '{resolved}'")
    return transport
