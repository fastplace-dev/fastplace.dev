"""Channel contract — registry, resolution, error paths, and fake channels.

Fakes here are the reference shape for later waves: a plain class with an
``async def send`` and captured calls, registered through the public API.
"""

from __future__ import annotations

from typing import Any

import pytest

from fastplace.mail import clear_mail_outbox, mail_outbox
from fastplace.notifications import (
    Channel,
    DatabaseChannel,
    MailChannel,
    Notifiable,
    Notification,
    NotificationError,
    channel_for,
    register_channel,
    reset_channels,
    send,
    unregister_channel,
)


@pytest.fixture(autouse=True)
def _clean_outbox():
    clear_mail_outbox()
    yield
    clear_mail_outbox()


class User(Notifiable):
    """The minimal notifiable: an email for mail, an id for database keying."""

    def __init__(self, id: int, email: str) -> None:
        self.id = id
        self.email = email


class Ping(Notification):
    """Declares nothing but via() — the smallest legal notification."""


class FakeChannel:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, Notification]] = []

    async def send(self, notifiable: Any, notification: Notification) -> str:
        self.calls.append((notifiable, notification))
        return "fake-ok"


# ---------------------------------------------------------------------------
# base — via default, keying
# ---------------------------------------------------------------------------


def test_via_defaults_to_mail():
    assert Ping().via(User(1, "u@example.test")) == ["mail"]


def test_notifiable_key_derivation():
    from fastplace.notifications import notifiable_key

    assert notifiable_key(User(7, "u@example.test")) == ("User", "7")
    assert notifiable_key(object()) == ("object", "")


def test_notifiable_key_maps_an_explicit_none_id_to_empty():
    from fastplace.notifications import notifiable_key

    class Idless:
        id = None

    assert notifiable_key(Idless()) == ("Idless", "")


def test_channel_protocol_runtime_check():
    assert isinstance(FakeChannel(), Channel)
    assert not isinstance(object(), Channel)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_builtins_are_registered():
    assert isinstance(channel_for("mail"), MailChannel)
    assert isinstance(channel_for("database"), DatabaseChannel)


def test_unknown_channel_raises():
    with pytest.raises(NotificationError, match="unknown notification channel 'nope'"):
        channel_for("nope")


def test_register_and_resolve_a_fake():
    fake = FakeChannel()
    register_channel("fake", fake)
    assert channel_for("fake") is fake


def test_reregistering_a_name_replaces_the_channel():
    first, second = FakeChannel(), FakeChannel()
    register_channel("fake", first)
    register_channel("fake", second)
    assert channel_for("fake") is second


def test_register_rejects_bad_input():
    with pytest.raises(NotificationError, match="non-empty"):
        register_channel("   ", FakeChannel())
    with pytest.raises(NotificationError, match="async send"):

        class SyncChannel:
            def send(self, notifiable: Any, notification: Notification) -> None:
                return None

        register_channel("sync", SyncChannel())


def test_unregister_and_reset_restore_builtins():
    unregister_channel("mail")
    with pytest.raises(NotificationError):
        channel_for("mail")
    reset_channels()
    assert isinstance(channel_for("mail"), MailChannel)


# ---------------------------------------------------------------------------
# dispatch — send() and the missing-builder errors
# ---------------------------------------------------------------------------


async def test_send_routes_through_a_registered_fake():
    fake = FakeChannel()
    register_channel("fake", fake)

    class ViaFake(Notification):
        def via(self, notifiable: Any) -> list[str]:
            return ["fake"]

    user = User(1, "u@example.test")
    notification = ViaFake()
    assert await send(user, notification) == ["fake-ok"]
    assert fake.calls == [(user, notification)]


async def test_send_accepts_a_list_of_notifiables():
    fake = FakeChannel()
    register_channel("fake", fake)

    class ViaFake(Notification):
        def via(self, notifiable: Any) -> list[str]:
            return ["fake"]

    users = [User(1, "a@example.test"), User(2, "b@example.test")]
    results = await send(users, ViaFake())
    assert len(results) == 2
    assert [u.email for u, _ in fake.calls] == ["a@example.test", "b@example.test"]


async def test_send_duck_types_notifiables_without_the_mixin():
    fake = FakeChannel()
    register_channel("fake", fake)

    class ViaFake(Notification):
        def via(self, notifiable: Any) -> list[str]:
            return ["fake"]

    plain: Any = type("Bare", (), {"id": 3, "email": "bare@example.test"})()
    results = await send([plain], ViaFake())
    assert results == ["fake-ok"]
    assert fake.calls[0][0].email == "bare@example.test"


async def test_via_listing_mail_without_to_mail_raises_at_send():
    class Mailless(Notification):
        def via(self, notifiable: Any) -> list[str]:
            return ["mail"]

    with pytest.raises(NotificationError, match=r"Mailless.*to_mail"):
        await send(User(1, "u@example.test"), Mailless())


async def test_via_listing_database_without_to_database_raises_at_send():
    class Rowless(Notification):
        def via(self, notifiable: Any) -> list[str]:
            return ["database"]

    with pytest.raises(NotificationError, match=r"Rowless.*to_database"):
        await send(User(1, "u@example.test"), Rowless())


async def test_to_mail_returning_a_non_message_raises():
    class BadBuilder(Notification):
        def via(self, notifiable: Any) -> list[str]:
            return ["mail"]

        def to_mail(self, notifiable: Any) -> Any:
            return "not a message"

    with pytest.raises(NotificationError, match="MailMessage"):
        await send(User(1, "u@example.test"), BadBuilder())
    assert mail_outbox() == []


async def test_send_rejects_an_unregistered_channel_name():
    """The send path resolves via() names too — not just channel_for()."""

    class ViaNowhere(Notification):
        def via(self, notifiable: Any) -> list[str]:
            return ["nowhere"]

    with pytest.raises(NotificationError, match="unknown notification channel 'nowhere'"):
        await send(User(1, "u@example.test"), ViaNowhere())
