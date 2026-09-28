"""Rich MailMessage — cc/bcc/reply_to, attachments, JSON-safe payloads (sweep-G3).

The six legacy fields stay byte-compatible; everything here is additive.
"""

from __future__ import annotations

import base64
import json
import sys
from typing import Any

import pytest

from fastplace.mail import (
    Mail,
    MailMessage,
    clear_mail_outbox,
    mail_outbox,
    message_as_dict,
    message_from_dict,
)
from fastplace.mail.message import Attachment


@pytest.fixture(autouse=True)
def _clean_outbox():
    clear_mail_outbox()
    yield
    clear_mail_outbox()


# ---------------------------------------------------------------------------
# data model — new fields default, construct, and round-trip
# ---------------------------------------------------------------------------


def test_new_fields_default_empty():
    message = MailMessage(subject="Hello", text="Body", to="x@example.test")
    assert message.cc == []
    assert message.bcc == []
    assert message.reply_to is None
    assert message.attachments == []


def test_new_fields_construct_from_kwargs():
    attachment = Attachment(filename="a.txt", content=b"hi")
    message = MailMessage(
        subject="Hello",
        text="Body",
        to="x@example.test",
        cc=["c@example.test"],
        bcc=["b@example.test"],
        reply_to="r@example.test",
        attachments=[attachment],
    )
    assert message.cc == ["c@example.test"]
    assert message.bcc == ["b@example.test"]
    assert message.reply_to == "r@example.test"
    assert message.attachments == [attachment]


def test_legacy_dict_roundtrip_unchanged():
    original = MailMessage(
        subject="Hello",
        text="Body",
        to="user@example.test",
        html="<p>Body</p>",
        from_address="app@example.test",
    )
    payload = message_as_dict(original)
    assert payload["subject"] == "Hello"
    assert payload["html"] == "<p>Body</p>"
    assert message_from_dict(payload) == original


def test_rich_dict_roundtrip():
    original = MailMessage(
        subject="Hello",
        text="Body",
        to="user@example.test",
        cc=["c@example.test"],
        bcc=["b@example.test"],
        reply_to="r@example.test",
    )
    original.attach(content=b"\x89PNG-data", filename="a.png", mime="image/png")
    rebuilt = message_from_dict(message_as_dict(original))
    assert rebuilt == original
    assert rebuilt.attachments[0].content == b"\x89PNG-data"
    assert rebuilt.attachments[0].mime == "image/png"


def test_rich_payload_is_json_safe():
    message = MailMessage(subject="Hello", text="Body", to="x@example.test")
    message.attach(content=bytes(range(256)), filename="blob.bin")
    payload = message_as_dict(message)
    assert isinstance(payload["attachments"], list)
    encoded = json.dumps(payload)
    assert "blob.bin" in encoded
    # The base64 content survives a real JSON round-trip (the queue boundary).
    assert message_from_dict(json.loads(encoded)) == message


def test_payload_tolerates_legacy_payloads_without_new_keys():
    legacy = {
        "subject": "Hello",
        "text": "Body",
        "to": "user@example.test",
        "html": None,
        "from_address": None,
        "from_name": None,
    }
    rebuilt = message_from_dict(legacy)
    assert rebuilt.cc == []
    assert rebuilt.bcc == []
    assert rebuilt.reply_to is None
    assert rebuilt.attachments == []


# ---------------------------------------------------------------------------
# fluent builders — chainable, validating
# ---------------------------------------------------------------------------


def test_builders_chain_and_mutate():
    message = MailMessage(subject="Hello", text="Body", to="x@example.test")
    result = (
        message.attach(content=b"one", filename="one.txt")
        .attach(content=b"two", filename="two.txt")
        .add_cc("c1@example.test", "c2@example.test")
        .add_bcc("b1@example.test")
        .set_reply_to("r@example.test")
    )
    assert result is message
    assert [a.filename for a in message.attachments] == ["one.txt", "two.txt"]
    assert message.cc == ["c1@example.test", "c2@example.test"]
    assert message.bcc == ["b1@example.test"]
    assert message.reply_to == "r@example.test"


def test_attach_rejects_both_and_neither_source():
    message = MailMessage(subject="Hello", text="Body", to="x@example.test")
    with pytest.raises(ValueError, match="exactly one"):
        message.attach(path="/tmp/a.txt", content=b"data", filename="a.txt")
    with pytest.raises(ValueError, match="exactly one"):
        message.attach(filename="a.txt")
    assert message.attachments == []


def test_attach_content_requires_a_filename():
    message = MailMessage(subject="Hello", text="Body", to="x@example.test")
    with pytest.raises(ValueError, match="filename"):
        message.attach(content=b"data")


def test_attach_rejects_non_bytes_content():
    message = MailMessage(subject="Hello", text="Body", to="x@example.test")
    with pytest.raises(ValueError, match="bytes"):
        message.attach(content="plain text", filename="a.txt")
    assert message.attachments == []
    message.attach(content=b"real bytes", filename="a.txt")  # bytes still accepted
    assert message.attachments[0].content == b"real bytes"


def test_attach_defaults_filename_from_path_basename():
    message = MailMessage(subject="Hello", text="Body", to="x@example.test")
    message.attach(path="/tmp/dir/report.pdf")
    assert message.attachments[0].filename == "report.pdf"
    assert message.attachments[0].path == "/tmp/dir/report.pdf"
    assert message.attachments[0].content is None


# ---------------------------------------------------------------------------
# Attachment — MIME resolution and payload pair
# ---------------------------------------------------------------------------


def test_attachment_payload_pair_is_json_safe():
    attachment = Attachment(filename="a.png", content=b"\x89PNG", mime="image/png")
    payload = attachment.to_payload()
    assert payload["content"] == base64.b64encode(b"\x89PNG").decode("ascii")
    json.dumps(payload)  # must not raise
    assert Attachment.from_payload(payload) == attachment


def test_attachment_payload_keeps_path_attachments_lightweight():
    payload = Attachment(filename="report.pdf", path="/tmp/report.pdf").to_payload()
    assert payload["path"] == "/tmp/report.pdf"
    assert payload["content"] is None
    rebuilt = Attachment.from_payload(payload)
    assert rebuilt.path == "/tmp/report.pdf"
    assert rebuilt.content is None


def test_attachment_resolved_mime_guesses_from_filename():
    assert Attachment(filename="photo.png").resolved_mime() == "image/png"
    assert Attachment(filename="archive.zip").resolved_mime() == "application/zip"
    unknown = Attachment(filename="data.unknownext")
    assert unknown.resolved_mime() == "application/octet-stream"


def test_attachment_explicit_mime_wins():
    assert Attachment(filename="a.bin", mime="application/x-custom").resolved_mime() == (
        "application/x-custom"
    )


def test_attachment_load_content_reads_path(tmp_path):
    file = tmp_path / "notes.txt"
    file.write_bytes(b"from disk")
    assert Attachment(filename="notes.txt", path=str(file)).load_content() == b"from disk"


def test_attachment_load_content_rejects_dangling():
    with pytest.raises(ValueError, match="neither content nor path"):
        Attachment(filename="a.txt").load_content()


# ---------------------------------------------------------------------------
# log transport — metadata only, never attachment bytes
# ---------------------------------------------------------------------------


async def test_log_line_carries_rich_fields_but_never_attachment_blobs(monkeypatch, tmp_path):
    from fastplace.mail import transports

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAIL_DRIVER", "log")
    message = MailMessage(
        subject="Hello",
        text="Body",
        to="x@example.test",
        cc=["c@example.test"],
        bcc=["b@example.test"],
        reply_to="r@example.test",
    )
    message.attach(content=b"secret-bytes", filename="a.txt")
    await Mail.deliver(message)

    raw = transports.MAIL_LOG_PATH.read_text(encoding="utf-8")
    assert "secret-bytes" not in raw
    entry = json.loads(raw.splitlines()[0])
    assert entry["cc"] == ["c@example.test"]
    assert entry["bcc"] == ["b@example.test"]
    assert entry["reply_to"] == "r@example.test"
    assert entry["attachments"] == [{"filename": "a.txt", "size": len(b"secret-bytes")}]


async def test_log_line_attachment_size_for_path_files(monkeypatch, tmp_path):
    from fastplace.mail import transports

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAIL_DRIVER", "log")
    file = tmp_path / "report.pdf"
    file.write_bytes(b"x" * 42)
    message = MailMessage(subject="Hello", text="Body", to="x@example.test")
    message.attach(path=str(file))
    await Mail.deliver(message)

    entry = json.loads(transports.MAIL_LOG_PATH.read_text(encoding="utf-8").splitlines()[0])
    assert entry["attachments"] == [{"filename": "report.pdf", "size": 42}]


# ---------------------------------------------------------------------------
# memory transport — stores the full rich message
# ---------------------------------------------------------------------------


async def test_memory_transport_stores_rich_message(monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "memory")
    message = MailMessage(subject="Hello", text="Body", to="x@example.test", cc=["c@example.test"])
    message.attach(content=b"payload", filename="a.bin", mime="application/octet-stream")
    await Mail.deliver(message)
    stored = mail_outbox()[0]
    assert stored.attachments[0].content == b"payload"
    assert stored.cc == ["c@example.test"]


async def test_mail_send_addresses_rich_message_unchanged(monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "memory")
    message = MailMessage(subject="Hello", text="Body", to="original@example.test")
    message.attach(content=b"data", filename="a.txt")
    final = await Mail.to("routed@example.test").send(message)
    assert final.to == "routed@example.test"
    assert mail_outbox()[0].to == "routed@example.test"
    assert mail_outbox()[0].attachments[0].filename == "a.txt"


# ---------------------------------------------------------------------------
# smtp transport — MIME structure
# ---------------------------------------------------------------------------


class _CaptureSmtp:
    letters: list[Any] = []

    @staticmethod
    async def send(letter, **kwargs):
        _CaptureSmtp.letters.append(letter)


@pytest.fixture(autouse=True)
def _capture_smtp_letters():
    _CaptureSmtp.letters.clear()
    yield
    _CaptureSmtp.letters.clear()


def _smtp_send(monkeypatch) -> None:
    monkeypatch.setenv("MAIL_DRIVER", "smtp")
    monkeypatch.setenv("QUEUE_DRIVER", "memory")
    monkeypatch.setitem(sys.modules, "aiosmtplib", _CaptureSmtp)


async def test_smtp_plain_message_stays_single_part(monkeypatch):
    _smtp_send(monkeypatch)
    await Mail.deliver(MailMessage(subject="Hello", text="Body", to="x@example.test"))
    letter = _CaptureSmtp.letters[-1]
    assert letter.is_multipart() is False
    assert letter["From"] == "fastplace@localhost"


async def test_smtp_rich_headers_only_when_set(monkeypatch):
    _smtp_send(monkeypatch)
    await Mail.deliver(MailMessage(subject="Hello", text="Body", to="x@example.test"))
    bare = _CaptureSmtp.letters[-1]
    assert bare["Cc"] is None
    assert bare["Bcc"] is None
    assert bare["Reply-To"] is None

    rich = MailMessage(
        subject="Hello",
        text="Body",
        to="x@example.test",
        cc=["c1@example.test", "c2@example.test"],
        bcc=["hidden@example.test"],
        reply_to="r@example.test",
    )
    await Mail.deliver(rich)
    headed = _CaptureSmtp.letters[-1]
    assert headed["Cc"] == "c1@example.test, c2@example.test"
    assert headed["Bcc"] == "hidden@example.test"
    assert headed["Reply-To"] == "r@example.test"


async def test_smtp_attachments_build_multipart_alternative_plus_parts(monkeypatch):
    _smtp_send(monkeypatch)
    message = MailMessage(subject="Hello", text="Body", to="x@example.test", html="<p>Body</p>")
    message.attach(content=b"\x89PNG", filename="a.png")
    message.attach(content=b"note text", filename="notes.txt")
    await Mail.deliver(message)

    letter = _CaptureSmtp.letters[-1]
    assert letter.is_multipart() is True
    types = [part.get_content_type() for part in letter.walk()]
    assert "text/plain" in types
    assert "text/html" in types
    assert "image/png" in types
    for part in letter.walk():
        if part.get_filename() == "a.png":
            assert part.get_payload(decode=True) == b"\x89PNG"
        if part.get_filename() == "notes.txt":
            assert part.get_payload(decode=True) == b"note text\n"


async def test_smtp_explicit_mime_wins_over_guess(monkeypatch):
    _smtp_send(monkeypatch)
    message = MailMessage(subject="Hello", text="Body", to="x@example.test")
    message.attach(content=b"data", filename="a.bin", mime="application/x-custom")
    await Mail.deliver(message)
    parts = [p for p in _CaptureSmtp.letters[-1].walk() if p.get_filename() == "a.bin"]
    assert parts[0].get_content_type() == "application/x-custom"


async def test_smtp_path_attachment_reads_disk(monkeypatch, tmp_path):
    file = tmp_path / "from-disk.pdf"
    file.write_bytes(b"%PDF-1.4 fake")
    _smtp_send(monkeypatch)
    message = MailMessage(subject="Hello", text="Body", to="x@example.test")
    message.attach(path=str(file))
    await Mail.deliver(message)
    parts = [p for p in _CaptureSmtp.letters[-1].walk() if p.get_filename() == "from-disk.pdf"]
    assert parts[0].get_payload(decode=True) == b"%PDF-1.4 fake"


async def test_smtp_queued_payload_delivers_rich_message(monkeypatch):
    """The queue path: dict payload (base64 attachments) rebuilds and sends."""
    _smtp_send(monkeypatch)
    message = MailMessage(subject="Hello", text="Body", to="x@example.test")
    message.attach(content=b"queued-bytes", filename="a.bin", mime="application/octet-stream")
    await Mail.deliver(message_from_dict(message_as_dict(message)))
    parts = [p for p in _CaptureSmtp.letters[-1].walk() if p.get_filename() == "a.bin"]
    assert parts[0].get_payload(decode=True) == b"queued-bytes"
