"""Notification core — the declarative base, the Notifiable mixin, keying.

A Notification declares WHERE it can go (``via``) and HOW (``to_<channel>``
payload builders); it never touches transports or storage. The built-in
channels are ``mail`` (an email through the Mail facade) and ``database``
(an in-app row); anything else registers through
:func:`fastplace.notifications.register_channel`. A via-listed channel with
no matching ``to_<channel>`` method fails at send time with a clear
:class:`NotificationError` — misrouting is a code bug, not a runtime race.
"""

from __future__ import annotations

from typing import Any

from fastplace.errors import FastplaceError


class NotificationError(FastplaceError):
    """A notification could not be routed, built, or registered."""

    status_code = 500
    default_message = "Notification error"


class Notification:
    """Base class: subclasses declare ``via()`` plus one ``to_<channel>``
    builder per channel they list. Everything else is channel plumbing."""

    def via(self, notifiable: Any) -> list[str]:
        """The channels this notification reaches ``notifiable`` through."""
        return ["mail"]


def notifiable_key(notifiable: Any) -> tuple[str, str]:
    """The database channel's routing key — (class name, id), both strings.

    String coercion keeps ORM integer ids and uuids in one portable column;
    an id-less notifiable (missing or None) keys as ``""`` so ad-hoc
    objects still record.
    """
    raw = getattr(notifiable, "id", None)
    return (type(notifiable).__name__, "" if raw is None else str(raw))


class Notifiable:
    """Mixin for anything that receives notifications.

    The mail channel reads ``notifiable.email``; the database channel keys
    rows on ``(class name, id)``. The ``notify`` method is the only surface
    application code needs: ``user.notify(InvoicePaid(invoice))``.
    """

    async def notify(self, notification: Notification) -> list[Any]:
        """Fan this notification out over its ``via()`` channels."""
        # Function-level import: channels.py needs the Notification type from
        # this module, so the cycle is broken at the use site.
        from fastplace.notifications.channels import send

        return await send(self, notification)
