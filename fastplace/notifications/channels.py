"""Channel registry + built-ins — how notifications actually leave the process.

Channels are named async objects; every name a ``via()`` lists must resolve
here at send time. The built-ins reuse the framework's own plumbing instead
of reinventing it: ``mail`` sends through the Mail facade (queueing
semantics inherited for free), and ``database`` writes a framework-owned
row (the cache-table pattern: dedicated metadata, idempotent create).
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from typing import Any, Protocol, runtime_checkable

from fastplace.mail import Mail, Mailable, MailMessage
from fastplace.notifications.base import Notification, NotificationError, notifiable_key
from fastplace.notifications.store import NotificationStore


@runtime_checkable
class Channel(Protocol):
    """The async surface every notification channel implements."""

    async def send(self, notifiable: Any, notification: Notification) -> Any: ...


class MailChannel:
    """The ``mail`` channel — builds ``to_mail()`` and sends via the Mail facade.

    The facade re-addresses the message to ``notifiable.email`` and owns the
    queue-or-deliver decision, so channel senders never see either.
    """

    async def send(self, notifiable: Any, notification: Notification) -> MailMessage:
        builder = getattr(notification, "to_mail", None)
        if builder is None:
            raise NotificationError(
                f"{type(notification).__name__} lists the 'mail' channel in via() "
                "but defines no to_mail(notifiable) builder"
            )
        message = builder(notifiable)
        if inspect.isawaitable(message):
            message = await message
        if not isinstance(message, (MailMessage, Mailable)):
            raise NotificationError(
                f"{type(notification).__name__}.to_mail() must return a MailMessage "
                f"or Mailable, got {type(message).__name__}"
            )
        email = getattr(notifiable, "email", None)
        if not email:
            raise NotificationError(
                f"{type(notifiable).__name__} has no email attribute — "
                "the mail channel cannot route"
            )
        return await Mail.to(email).send(message)


class DatabaseChannel:
    """The ``database`` channel — persists ``to_database()`` as a row.

    Rows key on ``(class name, id)``; the returned dict carries the row id,
    which callers pass back to ``NotificationStore.mark_read``.
    """

    def __init__(self, store: NotificationStore | None = None) -> None:
        self._store = store or NotificationStore()

    async def send(self, notifiable: Any, notification: Notification) -> dict[str, Any]:
        builder = getattr(notification, "to_database", None)
        if builder is None:
            raise NotificationError(
                f"{type(notification).__name__} lists the 'database' channel in via() "
                "but defines no to_database(notifiable) builder"
            )
        payload = builder(notifiable)
        if inspect.isawaitable(payload):
            payload = await payload
        if not isinstance(payload, dict):
            raise NotificationError(
                f"{type(notification).__name__}.to_database() must return a dict, "
                f"got {type(payload).__name__}"
            )
        notifiable_type, notifiable_id = notifiable_key(notifiable)
        return await self._store.record(
            notifiable_type=notifiable_type, notifiable_id=notifiable_id, payload=payload
        )


#: Registry of channel name -> object with ``async send``. Third parties and
#: test fakes join through ``register_channel`` — the public extension point.
_channels: dict[str, Any] = {}


def _is_async_channel(channel: Any) -> bool:
    """True when ``channel.send`` is awaitable-callable (mirrors mail transports)."""
    target = getattr(channel, "send", None)
    while isinstance(target, functools.partial):
        target = target.func
    return asyncio.iscoroutinefunction(target)


def register_channel(name: str, channel: Any) -> None:
    """Register a delivery channel under the name ``via()`` lists.

    Any object with an ``async def send(notifiable, notification)`` qualifies
    — applications, packages, and test fakes all use this one API.
    Re-registering a name replaces its channel.
    """
    if not isinstance(name, str) or not name.strip():
        raise NotificationError("channel name must be a non-empty string")
    if not _is_async_channel(channel):
        raise NotificationError(
            f"channel '{name}' must define an async send(notifiable, notification)"
        )
    _channels[name] = channel


def unregister_channel(name: str) -> None:
    """Drop a registered channel (test isolation and teardown)."""
    _channels.pop(name, None)


def channel_for(name: str) -> Any:
    """Resolve a channel by the name a ``via()`` listed."""
    channel = _channels.get(name)
    if channel is None:
        raise NotificationError(f"unknown notification channel '{name}'")
    return channel


def reset_channels() -> None:
    """Restore the built-ins — test isolation, and it drops any store state
    bound to a stale engine (tests that rebind the database call this)."""
    _channels.clear()
    register_channel("mail", MailChannel())
    register_channel("database", DatabaseChannel())


async def send(notifiable_or_list: Any, notification: Notification) -> list[Any]:
    """Fan a notification out over each notifiable's ``via()`` channels.

    Accepts one notifiable, or several at once as a ``list``/``tuple``/
    ``set``/``frozenset`` of them (the Notifiable mixin's ``notify``
    delegates here); returns the channel results in send order.
    """
    targets = (
        list(notifiable_or_list)
        if isinstance(notifiable_or_list, (list, tuple, set, frozenset))
        else [notifiable_or_list]
    )
    results: list[Any] = []
    for target in targets:
        for name in notification.via(target):
            results.append(await channel_for(name).send(target, notification))
    return results


reset_channels()  # import installs the built-ins — transports.py's pattern
