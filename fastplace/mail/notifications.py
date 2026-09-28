"""Auth notification emails — text-only, both carry the link plus expiry note.

These two builders predate the general notification system and stay as-is
(auth callsites depend on them byte-for-byte). For anything new — channel
routing, in-app rows, attachments — build a
:class:`fastplace.notifications.Notification` subclass instead.
"""

from __future__ import annotations

from fastplace.mail.message import MailMessage

_EXPIRY_NOTE = (
    "This link expires in 60 minutes. If you did not request it, no further action is required."
)


def verify_email_message(email: str, url: str) -> MailMessage:
    return MailMessage(
        subject="Verify your email address",
        text=f"Welcome! Confirm your email address by visiting:\n\n{url}\n\n{_EXPIRY_NOTE}",
        to=email,
    )


def reset_password_message(email: str, url: str) -> MailMessage:
    return MailMessage(
        subject="Reset your password",
        text=f"You requested a password reset. Visit:\n\n{url}\n\n{_EXPIRY_NOTE}",
        to=email,
    )
