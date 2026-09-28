"""Mail transports — built-ins (log, memory, smtp) plus the public registry.

Third-party drivers plug in with ``register_transport(name, async sender)``
and run via ``MAIL_DRIVER=<name>``. Richness is a transport concern: the
smtp driver builds the MIME tree (text/html alternative pair, attachment
parts), while the log driver records attachment names and sizes only — the
log is a debugging surface, never a delivery channel.
"""

from __future__ import annotations

import asyncio
import functools
import json
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from fastplace.config import config
from fastplace.errors import ConfigurationError
from fastplace.mail.message import Attachment, MailMessage

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


def _attachment_log_entry(attachment: Attachment) -> dict[str, str | int | None]:
    """Names + sizes only — file bytes never land in the log (log hygiene)."""
    if attachment.content is not None:
        size: int | None = len(attachment.content)
    elif attachment.path is not None:
        try:
            size = Path(attachment.path).stat().st_size
        except OSError:
            size = None  # a missing file must not break local dev logging
    else:
        size = None
    return {"filename": attachment.filename, "size": size}


async def send_via_log(message: MailMessage) -> None:
    """Append one JSON line per message to storage/logs/mail.log.

    Attachments log as names + sizes only; cc/bcc/reply_to ride along for
    local visibility of the full envelope.
    """
    MAIL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "subject": message.subject,
            "text": message.text,
            "to": message.to,
            "html": message.html,
            "from_address": message.from_address,
            "from_name": message.from_name,
            "cc": message.cc,
            "bcc": message.bcc,
            "reply_to": message.reply_to,
            "attachments": [_attachment_log_entry(a) for a in message.attachments],
        },
        ensure_ascii=False,
    )
    with MAIL_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


async def send_via_memory(message: MailMessage) -> None:
    """Capture the message in-process (tests and local dev)."""
    _outbox.append(message)


def _add_attachment(letter: EmailMessage, attachment: Attachment) -> None:
    """Append one MIME part — text/* needs str data (the email API requires it).

    A hand-set mime without a subtype falls back to application/octet-stream
    rather than tripping the content manager on an empty subtype.
    """
    maintype, _, subtype = attachment.resolved_mime().partition("/")
    if not subtype:
        maintype, subtype = "application", "octet-stream"
    data = attachment.load_content()
    if maintype == "text":
        letter.add_attachment(
            data.decode("utf-8", "replace"), subtype=subtype, filename=attachment.filename
        )
    else:
        letter.add_attachment(
            data, maintype=maintype, subtype=subtype, filename=attachment.filename
        )


async def send_via_smtp(message: MailMessage) -> None:
    """Deliver through aiosmtplib (`pip install 'fastplace[mail]'`).

    Rich messages build the standard MIME tree: text + html as the
    alternative pair, attachments as sibling parts under a mixed root.
    Plain messages keep the simple single-part form.
    """
    try:
        import aiosmtplib
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ConfigurationError(
            "MAIL_DRIVER=smtp requires aiosmtplib — pip install 'fastplace[mail]'"
        ) from exc

    from email.utils import formataddr

    letter = EmailMessage()
    address = message.from_address or str(
        config("MAIL_FROM_ADDRESS", default="fastplace@localhost")
    )
    letter["From"] = formataddr((message.from_name or "", address))
    letter["To"] = message.to
    letter["Subject"] = message.subject
    if message.cc:
        letter["Cc"] = ", ".join(message.cc)
    if message.bcc:
        # aiosmtplib reads the Bcc header for envelope recipients and strips
        # it before sending — set it, never pre-strip it.
        letter["Bcc"] = ", ".join(message.bcc)
    if message.reply_to:
        letter["Reply-To"] = message.reply_to
    letter.set_content(message.text)
    if message.html:
        letter.add_alternative(message.html, subtype="html")
    for attachment in message.attachments:
        _add_attachment(letter, attachment)

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


#: Registry of MAIL_DRIVER name -> async sender ``(MailMessage) -> None``.
#: Third-party drivers join through ``register_transport`` — the public
#: extension point — never by touching this dict directly.
_TRANSPORTS: dict[str, Any] = {}


def _is_async_sender(sender: Any) -> bool:
    """True when ``sender`` is awaitable-callable — an ``async def`` function.

    ``functools.partial`` wrappers are unwrapped so a curried async sender
    still registers; anything else sync (a plain function or lambda) fails
    here instead of exploding at first send.
    """
    target = sender
    while isinstance(target, functools.partial):
        target = target.func
    return asyncio.iscoroutinefunction(target)


def register_transport(name: str, sender: Any) -> None:
    """Register a mail transport callable under a ``MAIL_DRIVER`` name.

    The public extension API for third-party drivers: applications and
    packages call this at boot and select the driver with
    ``MAIL_DRIVER=<name>``. The sender must be an ``async def`` callable
    taking one ``MailMessage`` — a sync callable is rejected at registration
    rather than at first send. Re-registering a name replaces its sender —
    the built-ins (log/memory/smtp) register through this same API below.
    """
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError("transport name must be a non-empty string")
    if not callable(sender) or not _is_async_sender(sender):
        raise ConfigurationError(
            f"transport '{name}' must be an async callable "
            "(async def send(message: MailMessage) -> None)"
        )
    _TRANSPORTS[name] = sender


def unregister_transport(name: str) -> None:
    """Drop a registered transport (test isolation and teardown)."""
    _TRANSPORTS.pop(name, None)


register_transport("log", send_via_log)
register_transport("memory", send_via_memory)
register_transport("smtp", send_via_smtp)


def transport_for(driver: str | None = None) -> Any:
    """Resolve the transport callable for a MAIL_DRIVER (default: config)."""
    resolved = driver or str(config("MAIL_DRIVER", default="log") or "log")
    transport = _TRANSPORTS.get(resolved)
    if transport is None:
        raise ConfigurationError(f"unknown MAIL_DRIVER '{resolved}'")
    return transport
