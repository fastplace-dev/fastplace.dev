"""Fastplace mail — a minimal facade over pluggable transports (spec §4.8).

Drivers: ``log`` (default), ``memory`` (tests), ``smtp`` (optional extra
``fastplace[mail]``) — plus anything registered via
``register_transport(name, async sender)`` (third-party providers). Sending
queues ONLY when both MAIL_DRIVER=smtp and QUEUE_DRIVER=saq — local drivers
deliver inline so tests and dev see work immediately.
"""

from __future__ import annotations

import dataclasses

from fastplace.config import config
from fastplace.mail.message import MailMessage, message_as_dict, message_from_dict
from fastplace.mail.transports import (
    clear_mail_outbox,
    mail_outbox,
    register_transport,
    send_via_log,
    send_via_memory,
    send_via_smtp,
    transport_for,
    unregister_transport,
)

__all__ = [
    "Mail",
    "MailMessage",
    "clear_mail_outbox",
    "mail_outbox",
    "message_as_dict",
    "message_from_dict",
    "register_transport",
    "send_via_log",
    "send_via_memory",
    "send_via_smtp",
    "transport_for",
    "unregister_transport",
]


def _should_queue() -> bool:
    # SMTP delivery can take seconds — queue it when a real queue exists;
    # every other driver is cheap and stays inline.
    return (
        str(config("MAIL_DRIVER", default="log") or "log") == "smtp"
        and str(config("QUEUE_DRIVER", default="memory") or "memory") == "saq"
    )


class Mail:
    """The entry point application code sends through: ``Mail.to(addr).send(msg)``."""

    def __init__(self, to: str) -> None:
        self._to = to

    @classmethod
    def to(cls, address: str) -> Mail:
        return cls(address)

    async def send(self, message: MailMessage) -> MailMessage:
        """Address the message, then queue (smtp+saq) or deliver inline."""
        final = dataclasses.replace(message, to=self._to)
        if _should_queue():
            from fastplace.queue import queue

            await queue().dispatch("mail_send", message=message_as_dict(final))
            return final
        await self.deliver(final)
        return final

    @classmethod
    async def deliver(cls, message: MailMessage) -> None:
        """Run a transport NOW — the queued job's entry point (never re-enqueues)."""
        outbound = (
            message
            if message.from_address
            else dataclasses.replace(
                message,
                from_address=str(config("MAIL_FROM_ADDRESS", default="fastplace@localhost")),
            )
        )
        if outbound.from_name is None:
            configured_name = str(config("MAIL_FROM_NAME", default="") or "") or None
            if configured_name is not None:
                outbound = dataclasses.replace(outbound, from_name=configured_name)
        await transport_for()(outbound)
