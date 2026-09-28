"""Notifications — one declarative class per message, fanned out over named channels.

Applications subclass :class:`Notification`, declare ``via()`` and one
``to_<channel>`` payload builder per channel, and hand it to any notifiable:
``user.notify(InvoicePaid(invoice))``. The mail and database built-ins reuse
the Mail facade and a framework-owned table respectively; every other
channel (Slack, webhooks, test fakes) registers through
:func:`register_channel` with a two-method object. Misconfiguration — a
via-listed channel with no builder, an unknown channel name — fails at send
time with :class:`NotificationError`, never silently.
"""

from __future__ import annotations

from fastplace.notifications.base import (
    Notifiable,
    Notification,
    NotificationError,
    notifiable_key,
)
from fastplace.notifications.channels import (
    Channel,
    DatabaseChannel,
    MailChannel,
    channel_for,
    register_channel,
    reset_channels,
    send,
    unregister_channel,
)
from fastplace.notifications.store import NotificationStore

__all__ = [
    "Channel",
    "DatabaseChannel",
    "MailChannel",
    "Notification",
    "NotificationError",
    "NotificationStore",
    "Notifiable",
    "channel_for",
    "notifiable_key",
    "register_channel",
    "reset_channels",
    "send",
    "unregister_channel",
]
