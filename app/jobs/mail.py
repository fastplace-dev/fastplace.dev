"""Mail delivery job + the Registered verification listener.

``mail_send`` is the queue entry point (queued only when MAIL_DRIVER=smtp
and QUEUE_DRIVER=saq — see fastplace.mail.Mail.send); the listener runs
in-process (``Registered`` has no same-named @Job, so dispatch never
auto-enqueues it).
"""

from __future__ import annotations

import logging

from fastplace.errors import ConfigurationError
from fastplace.events import DomainEvent, listen
from fastplace.mail import Mail, message_from_dict
from fastplace.queue import Job

logger = logging.getLogger(__name__)


@Job(name="mail_send")
async def mail_send(message: dict) -> None:
    """Deliver one queued MailMessage payload (param name matters: saq
    hijacks handler kwargs named timeout/ttl/kwargs)."""
    await Mail.deliver(message_from_dict(message))


async def send_registration_verification(event: DomainEvent) -> None:
    """Mail the verification link for a freshly registered account."""
    from app.modules.accounts.services.verification_service import VerificationService

    email = str(event.payload["email"])
    try:
        await VerificationService().send_link(int(event.payload["user_id"]), email)
    except ConfigurationError:
        # Empty APP_KEY is the documented dev default (config/app.py) and the
        # account is already committed by the time this listener runs — skip
        # the signed link rather than fail the whole registration.
        logger.warning("APP_KEY is not set — skipping verification email for %s", email)


# listen() is a plain function, NOT a decorator factory — explicit call.
listen("Registered", send_registration_verification)
