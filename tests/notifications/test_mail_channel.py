"""The mail built-in — to_mail() builders ride the Mail facade end to end.

Queueing semantics are inherited, not reimplemented: MAIL_DRIVER=smtp with
QUEUE_DRIVER=saq queues, everything else delivers inline (spec §4.8 rule).
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

import fastplace.queue as queue_module
from fastplace.mail import MailMessage, clear_mail_outbox, mail_outbox
from fastplace.notifications import Notifiable, Notification, NotificationError, send


@pytest.fixture(autouse=True)
def _clean_outbox():
    clear_mail_outbox()
    yield
    clear_mail_outbox()


class Member(Notifiable):
    def __init__(self, id: int, email: str, name: str) -> None:
        self.id = id
        self.email = email
        self.name = name


class Welcome(Notification):
    def to_mail(self, notifiable: Any) -> MailMessage:
        return MailMessage(
            subject="Welcome",
            text=f"Hi {notifiable.name}, welcome aboard.",
            to="ignored@example.test",  # the facade re-addresses to the notifiable
        )


class LateWelcome(Notification):
    async def to_mail(self, notifiable: Any) -> MailMessage:  # awaitable builder
        return MailMessage(subject="Welcome (async)", text="Hello", to="x@example.test")


class QueuedWelcome(Notification):
    """Rich message so the queued payload proves cc/bcc/attachment survival."""

    def to_mail(self, notifiable: Any) -> MailMessage:
        return (
            MailMessage(
                subject="Welcome (queued)",
                text=f"Hi {notifiable.name}",
                to="ignored@example.test",
            )
            .add_bcc("audit@example.test")
            .attach(content=b"receipt-bytes", filename="receipt.txt")
        )


async def test_notify_delivers_through_the_mail_facade(monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "memory")
    member = Member(1, "member@example.test", "Ada")
    await member.notify(Welcome())
    delivered = mail_outbox()[0]
    assert delivered.to == "member@example.test"  # the notifiable decides the destination
    assert delivered.subject == "Welcome"
    assert "Ada" in delivered.text


async def test_awaitable_to_mail_builder_is_supported(monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "memory")
    results = await send(Member(2, "a@example.test", "Grace"), LateWelcome())
    assert mail_outbox()[0].subject == "Welcome (async)"
    assert results[0].to == "a@example.test"


async def test_mail_channel_raises_when_the_notifiable_has_no_email():
    class EmaillessBuilder(Notification):
        def to_mail(self, notifiable: Any) -> MailMessage:
            return MailMessage(subject="Hello", text="Body", to="x@example.test")

    bare: Any = type("Bare", (), {"id": 1})()  # duck-typed notifiable, no email attribute
    with pytest.raises(NotificationError, match="no email"):
        await send(bare, EmaillessBuilder())
    assert mail_outbox() == []


class StubQueue:
    def __init__(self):
        self.dispatched: list[tuple] = []

    async def dispatch(self, name, **kwargs):
        self.dispatched.append((name, kwargs))


async def test_queueing_semantics_are_inherited(monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "smtp")
    monkeypatch.setenv("QUEUE_DRIVER", "saq")
    stub = StubQueue()
    monkeypatch.setattr(queue_module, "_default_queue", stub)
    await Member(3, "q@example.test", "Linus").notify(QueuedWelcome())
    name, kwargs = stub.dispatched[0]
    assert name == "mail_send"
    payload = kwargs["message"]
    assert payload["to"] == "q@example.test"
    # The queue boundary carries the full rich message, byte-exact.
    assert payload["bcc"] == ["audit@example.test"]
    assert payload["attachments"][0]["content"] == base64.b64encode(b"receipt-bytes").decode(
        "ascii"
    )
    assert mail_outbox() == []
