"""Notifications fake — record fan-out legs instead of delivering them.

``install()`` swaps every registered channel for a recording twin that still
builds the ``to_<channel>`` payload (a missing builder fails as loudly as the
production channel) but performs no IO: mail is not sent, no database row is
written. ``restore()`` reinstates the exact originals — including
application-registered custom channels. The ``notifications`` fixture brackets
a test with both.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from fastplace.notifications import NotificationError, notifiable_key, register_channel
from fastplace.notifications.base import Notification


@dataclass
class SentNotification:
    """One recorded fan-out leg."""

    notifiable_type: str
    notifiable_id: Any
    notification: Notification
    channel: str
    payload: Any


class _RecordingChannel:
    """Stand-in for one channel name: build, record, return — no delivery."""

    def __init__(self, fake: FakeNotifications, name: str) -> None:
        self._fake = fake
        self._name = name

    async def send(self, notifiable: Any, notification: Notification) -> Any:
        builder = getattr(notification, f"to_{self._name}", None)
        if builder is None:
            raise NotificationError(
                f"{type(notification).__name__} lists the '{self._name}' channel in via() "
                f"but defines no to_{self._name}(notifiable) builder"
            )
        payload = builder(notifiable)
        if inspect.isawaitable(payload):
            payload = await payload
        notifiable_type, notifiable_id = notifiable_key(notifiable)
        self._fake.sent.append(
            SentNotification(
                notifiable_type=notifiable_type,
                notifiable_id=notifiable_id,
                notification=notification,
                channel=self._name,
                payload=payload,
            )
        )
        return payload


class FakeNotifications:
    """Assertion surface over notifications sent during a test."""

    def __init__(self) -> None:
        self.sent: list[SentNotification] = []
        self._originals: dict[str, Any] = {}

    # -- install/restore ------------------------------------------------------

    def install(self) -> None:
        """Replace every registered channel with its recording twin."""
        from fastplace.notifications import channels as channels_module

        self._originals = dict(channels_module._channels)
        for name in self._originals:
            register_channel(name, self._recording_channel(name))

    def restore(self) -> None:
        """Reinstate the channels ``install()`` replaced."""
        for name, original in self._originals.items():
            register_channel(name, original)
        self._originals = {}

    def _recording_channel(self, name: str) -> _RecordingChannel:
        return _RecordingChannel(self, name)

    # -- queries ---------------------------------------------------------------

    def _filter(
        self,
        notification: type[Notification] | str | None,
        to: Any,
        channel: str | None,
        match: dict[str, Any] | None,
    ) -> list[SentNotification]:
        wanted_key = None if to is None else (to if isinstance(to, tuple) else notifiable_key(to))
        records: list[SentNotification] = []
        for record in self.sent:
            if notification is not None:
                if isinstance(notification, str):
                    if type(record.notification).__name__ != notification:
                        continue
                elif not isinstance(record.notification, notification):
                    continue
            if wanted_key is not None:
                if (record.notifiable_type, record.notifiable_id) != wanted_key:
                    continue
            if channel is not None and record.channel != channel:
                continue
            if match is not None:
                if not isinstance(record.payload, dict) or not all(
                    record.payload.get(key) == value for key, value in match.items()
                ):
                    continue
            records.append(record)
        return records

    def records(
        self,
        notification: type[Notification] | str | None = None,
        *,
        to: Any = None,
        channel: str | None = None,
        match: dict[str, Any] | None = None,
    ) -> list[SentNotification]:
        """The sent legs matching every given filter, in send order."""
        return self._filter(notification, to, channel, match)

    # -- assertions -------------------------------------------------------------

    def assert_sent(
        self,
        notification: type[Notification] | str | None = None,
        *,
        to: Any = None,
        channel: str | None = None,
        match: dict[str, Any] | None = None,
        times: int | None = None,
    ) -> list[SentNotification]:
        matches = self._filter(notification, to, channel, match)
        if times is not None:
            if len(matches) != times:
                raise AssertionError(
                    f"expected {times} matching notification(s), got {len(matches)}; "
                    f"sent: {self._describe()}"
                )
        elif not matches:
            raise AssertionError(f"no matching notification sent; sent: {self._describe()}")
        return matches

    def assert_not_sent(
        self,
        notification: type[Notification] | str | None = None,
        *,
        to: Any = None,
        channel: str | None = None,
        match: dict[str, Any] | None = None,
    ) -> None:
        matches = self._filter(notification, to, channel, match)
        if matches:
            raise AssertionError(
                f"expected no matching notification, found {len(matches)}: {self._describe()}"
            )

    def assert_sent_count(self, count: int) -> None:
        if len(self.sent) != count:
            raise AssertionError(
                f"expected {count} notification leg(s) sent, got {len(self.sent)}: "
                f"{self._describe()}"
            )

    def assert_nothing_sent(self) -> None:
        self.assert_sent_count(0)

    def _describe(self) -> str:
        if not self.sent:
            return "<nothing>"
        return "\n".join(
            f"  - {type(r.notification).__name__} -> {r.notifiable_type}#{r.notifiable_id} "
            f"via {r.channel}"
            for r in self.sent
        )


__all__ = ["FakeNotifications", "SentNotification"]
