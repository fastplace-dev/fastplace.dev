"""FakeNotifications — records fan-out legs instead of delivering them."""

from __future__ import annotations

import pytest


class _User:
    def __init__(self, id: int, email: str) -> None:
        self.id = id
        self.email = email


def _notification_classes():
    from fastplace.mail.message import MailMessage
    from fastplace.notifications import Notification

    class InvoicePaid(Notification):
        def via(self, notifiable):
            return ["mail", "database"]

        def to_mail(self, notifiable):
            return MailMessage(subject="Paid", text="thanks", to=notifiable.email)

        def to_database(self, notifiable):
            return {"kind": "invoice", "amount": 42}

    class ServerError(Notification):
        def via(self, notifiable):
            return ["database"]

        def to_database(self, notifiable):
            return {"kind": "error"}

    return InvoicePaid, ServerError


async def test_fake_records_every_channel_leg_without_delivery():
    from fastplace.mail.transports import clear_mail_outbox, mail_outbox
    from fastplace.notifications import send
    from fastplace.testing import FakeNotifications

    InvoicePaid, _ = _notification_classes()
    user = _User(7, "a@example.test")

    fake = FakeNotifications()
    fake.install()
    try:
        clear_mail_outbox()
        results = await send(user, InvoicePaid())
    finally:
        fake.restore()
        clear_mail_outbox()

    # The fan-out ran (two legs, payloads built) but nothing left the process.
    assert results[0].subject == "Paid"
    assert results[1] == {"kind": "invoice", "amount": 42}
    assert mail_outbox() == []
    assert len(fake.sent) == 2
    assert fake.sent[0].channel == "mail"
    assert fake.sent[0].payload.subject == "Paid"
    assert fake.sent[1].channel == "database"
    assert fake.sent[1].payload == {"kind": "invoice", "amount": 42}
    assert fake.sent[1].notifiable_type == "_User"
    assert fake.sent[1].notifiable_id == "7"


def test_fake_restore_reinstates_the_original_channels():
    from fastplace.notifications import MailChannel, channel_for, reset_channels
    from fastplace.testing import FakeNotifications

    reset_channels()
    before = channel_for("mail")
    fake = FakeNotifications()
    fake.install()
    try:
        assert not isinstance(channel_for("mail"), MailChannel)
    finally:
        fake.restore()
    assert channel_for("mail") is before
    reset_channels()


async def test_async_builders_are_awaited():
    from fastplace.notifications import Notification, register_channel, reset_channels, send
    from fastplace.testing import FakeNotifications

    class Ping(Notification):
        def via(self, notifiable):
            return ["custom"]

        async def to_custom(self, notifiable):
            return {"pong": True}

    async def _slack_send(notifiable, notification):
        raise AssertionError("the recording channel must replace the custom channel too")

    register_channel("custom", type("Slack", (), {"send": staticmethod(_slack_send)})())
    fake = FakeNotifications()
    fake.install()
    try:
        results = await send(_User(1, "x@example.test"), Ping())
    finally:
        fake.restore()
        reset_channels()

    assert results == [{"pong": True}]
    fake.assert_sent(channel="custom", match={"pong": True})


async def test_missing_builder_still_fails_loudly():
    from fastplace.notifications import Notification, reset_channels
    from fastplace.testing import FakeNotifications

    class Broken(Notification):
        def via(self, notifiable):
            return ["database"]

        # no to_database builder

    fake = FakeNotifications()
    fake.install()
    try:
        from fastplace.errors import FastplaceError

        with pytest.raises(FastplaceError, match="to_database"):
            await fake._recording_channel("database").send(_User(1, "x@example.test"), Broken())
    finally:
        fake.restore()
        reset_channels()


# -- assertions ---------------------------------------------------------------


def _populated_fake():
    from fastplace.testing import FakeNotifications, SentNotification

    InvoicePaid, ServerError = _notification_classes()
    fake = FakeNotifications()
    fake.sent = [
        SentNotification(
            notifiable_type="_User",
            notifiable_id="7",
            notification=InvoicePaid(),
            channel="mail",
            payload=None,
        ),
        SentNotification(
            notifiable_type="_User",
            notifiable_id="8",
            notification=ServerError(),
            channel="database",
            payload={"kind": "error"},
        ),
    ]
    return fake, InvoicePaid, ServerError


def test_assert_sent_by_notification_type():
    fake, InvoicePaid, ServerError = _populated_fake()
    fake.assert_sent(InvoicePaid)
    fake.assert_sent(ServerError, channel="database")


def test_assert_sent_to_a_notifiable():
    fake, _, _ = _populated_fake()
    fake.assert_sent(to=_User(7, "any@example.test"))
    with pytest.raises(AssertionError):
        fake.assert_sent(to=_User(9, "any@example.test"))


def test_assert_sent_with_match_and_times():
    fake, _, ServerError = _populated_fake()
    fake.assert_sent(ServerError, match={"kind": "error"}, times=1)
    with pytest.raises(AssertionError):
        fake.assert_sent(ServerError, match={"kind": "info"})


def test_assert_not_sent_and_counts():
    fake, InvoicePaid, _ = _populated_fake()
    fake.assert_not_sent(to=_User(99, "x@example.test"))
    fake.assert_sent_count(2)
    with pytest.raises(AssertionError):
        fake.assert_not_sent(InvoicePaid)
