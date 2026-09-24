"""MAIL_FROM_NAME — the display half of the outbound sender (spec §4.8)."""

from __future__ import annotations

import json
import sys

import pytest

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


def test_dict_roundtrip_keeps_from_name():
    original = MailMessage(
        subject="Hello",
        text="Body",
        to="user@example.test",
        from_address="app@example.test",
        from_name="Fastplace App",
    )
    rebuilt = message_from_dict(message_as_dict(original))
    assert rebuilt == original
    assert rebuilt.from_name == "Fastplace App"


async def test_deliver_fills_from_name_from_config(monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "memory")
    monkeypatch.setenv("MAIL_FROM_NAME", "Fastplace App")
    await Mail.deliver(MailMessage(subject="Hello", text="Body", to="x@example.test"))
    assert mail_outbox()[0].from_address == "fastplace@localhost"
    assert mail_outbox()[0].from_name == "Fastplace App"


async def test_deliver_without_config_name_stays_none(monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "memory")
    monkeypatch.delenv("MAIL_FROM_NAME", raising=False)
    await Mail.deliver(MailMessage(subject="Hello", text="Body", to="x@example.test"))
    assert mail_outbox()[0].from_name is None


async def test_log_line_carries_from_name(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAIL_DRIVER", "log")
    monkeypatch.setenv("MAIL_FROM_NAME", "Fastplace App")
    await Mail.to("someone@example.test").send(
        MailMessage(subject="Hello", text="Body", to="x@example.test")
    )
    line = json.loads(transports.MAIL_LOG_PATH.read_text(encoding="utf-8").splitlines()[0])
    assert line["from_name"] == "Fastplace App"
    assert line["from_address"] == "fastplace@localhost"


class _CaptureSmtp:
    letters: list[object] = []

    @staticmethod
    async def send(letter, **kwargs):
        _CaptureSmtp.letters.append(letter)


async def test_smtp_from_header_combines_name_and_address(monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "smtp")
    monkeypatch.setenv("QUEUE_DRIVER", "memory")
    monkeypatch.setenv("MAIL_FROM_NAME", "Firoz Anam")
    monkeypatch.setitem(sys.modules, "aiosmtplib", _CaptureSmtp)
    await Mail.to("someone@example.test").send(
        MailMessage(subject="Hello", text="Body", to="someone@example.test")
    )
    assert _CaptureSmtp.letters[-1]["From"] == "Firoz Anam <fastplace@localhost>"


async def test_smtp_from_header_bare_without_name(monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "smtp")
    monkeypatch.setenv("QUEUE_DRIVER", "memory")
    monkeypatch.delenv("MAIL_FROM_NAME", raising=False)
    monkeypatch.setitem(sys.modules, "aiosmtplib", _CaptureSmtp)
    await Mail.to("someone@example.test").send(
        MailMessage(subject="Hello", text="Body", to="someone@example.test")
    )
    assert _CaptureSmtp.letters[-1]["From"] == "fastplace@localhost"
