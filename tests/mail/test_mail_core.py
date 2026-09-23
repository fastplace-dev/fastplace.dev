"""Mail core — message value, transports, queue-aware facade (spec §4.8)."""

from __future__ import annotations

import json
import sys

import pytest

import fastplace.queue as queue_module
from fastplace.errors import ConfigurationError
from fastplace.mail import (
    Mail,
    MailMessage,
    clear_mail_outbox,
    mail_outbox,
    message_as_dict,
    message_from_dict,
    transports,
)


@pytest.fixture(autouse=True)
def _clean_outbox():
    clear_mail_outbox()
    yield
    clear_mail_outbox()


def test_dict_roundtrip():
    original = MailMessage(
        subject="Hello",
        text="Body",
        to="user@example.test",
        html="<p>Body</p>",
        from_address="app@example.test",
    )
    assert message_from_dict(message_as_dict(original)) == original


async def test_memory_driver_captures(monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "memory")
    await Mail.to("someone@example.test").send(
        MailMessage(subject="Hello", text="Body", to="x@example.test")
    )
    assert mail_outbox()[0].to == "someone@example.test"
    assert mail_outbox()[0].subject == "Hello"


async def test_log_driver_appends_json_line(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAIL_DRIVER", "log")
    await Mail.to("someone@example.test").send(
        MailMessage(subject="Hello", text="Body", to="x@example.test")
    )
    lines = transports.MAIL_LOG_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["to"] == "someone@example.test"


class StubQueue:
    def __init__(self):
        self.dispatched: list[tuple] = []

    async def dispatch(self, name, **kwargs):
        self.dispatched.append((name, kwargs))


async def test_smtp_plus_saq_queues(monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "smtp")
    monkeypatch.setenv("QUEUE_DRIVER", "saq")
    stub = StubQueue()
    monkeypatch.setattr(queue_module, "_default_queue", stub)
    final = await Mail.to("someone@example.test").send(
        MailMessage(subject="Hello", text="Body", to="x@example.test")
    )
    assert stub.dispatched[0][0] == "mail_send"
    assert stub.dispatched[0][1]["message"]["to"] == "someone@example.test"
    assert final.to == "someone@example.test"
    assert mail_outbox() == []


async def test_smtp_without_aiosmtplib_raises(monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "smtp")
    monkeypatch.setenv("QUEUE_DRIVER", "memory")
    monkeypatch.setitem(sys.modules, "aiosmtplib", None)
    with pytest.raises(ConfigurationError, match=r"fastplace\[mail\]"):
        await Mail.to("someone@example.test").send(
            MailMessage(subject="Hello", text="Body", to="x@example.test")
        )


class StubAiosmtplib:
    sent: list[dict] = []

    @staticmethod
    async def send(letter, **kwargs):
        StubAiosmtplib.sent.append(kwargs)


async def test_smtp_delivery_via_stub(monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "smtp")
    monkeypatch.setenv("QUEUE_DRIVER", "memory")
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_PORT", "587")
    monkeypatch.setenv("MAIL_ENCRYPTION", "starttls")
    monkeypatch.setitem(sys.modules, "aiosmtplib", StubAiosmtplib)
    await Mail.to("someone@example.test").send(
        MailMessage(subject="Hello", text="Body", to="x@example.test")
    )
    kwargs = StubAiosmtplib.sent[-1]
    assert kwargs["hostname"] == "smtp.example.test"
    assert kwargs["port"] == 587
    assert kwargs["start_tls"] is True
    assert kwargs["use_tls"] is False


async def test_deliver_runs_transport_now(monkeypatch):
    delivered: list[MailMessage] = []

    async def fake_transport(message):
        delivered.append(message)

    import fastplace.mail as mail_package

    monkeypatch.setattr(mail_package, "transport_for", lambda driver=None: fake_transport)
    await Mail.deliver(MailMessage(subject="Hello", text="Body", to="x@example.test"))
    assert delivered[0].from_address == "fastplace@localhost"


def test_unknown_driver_raises():
    with pytest.raises(ConfigurationError, match="unknown MAIL_DRIVER"):
        transports.transport_for("carrier-pigeon")
