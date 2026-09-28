"""MailMessage — the JSON-safe value emails carry through queue and transports.

Richness (cc/bcc/reply_to/attachments) lives on the dataclass, not on a
builder wrapper, because the queue boundary serializes the dataclass
directly: ``message_as_dict`` payloads must carry everything a worker needs
to rebuild and send. Attachments ride as base64 ``content`` (or a
worker-local ``path``) so a queued message survives the dict round-trip
byte-exact; transports resolve the real bytes at send time.

The fluent builders mutate and return ``self``. They carry verb names
(``add_cc``/``add_bcc``/``set_reply_to``) because a dataclass field and a
method cannot share a name — the fields keep the payload/serialization
identity, so the builders yield.
"""

from __future__ import annotations

import base64
import dataclasses
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Attachment:
    """One file an email carries — inline bytes or a filesystem path.

    Exactly one of ``content``/``path`` is meaningful; the ``attach`` builder
    enforces it at build time and :meth:`load_content` at send time. ``mime``
    falls back to a ``mimetypes`` guess on the filename when unset.
    """

    filename: str
    content: bytes | None = None
    path: str | None = None
    mime: str | None = None

    def resolved_mime(self) -> str:
        """The effective MIME type — explicit ``mime``, else a filename guess."""
        return self.mime or mimetypes.guess_type(self.filename)[0] or "application/octet-stream"

    def load_content(self) -> bytes:
        """The attachment's bytes — inline content, else read ``path``."""
        if self.content is not None:
            return self.content
        if self.path is not None:
            return Path(self.path).read_bytes()
        raise ValueError(f"attachment '{self.filename}' has neither content nor path")

    def to_payload(self) -> dict[str, str | None]:
        """The JSON-safe dict (content base64) queued payloads carry."""
        return {
            "filename": self.filename,
            "content": (
                base64.b64encode(self.content).decode("ascii") if self.content is not None else None
            ),
            "path": self.path,
            "mime": self.mime,
        }

    @classmethod
    def from_payload(cls, data: dict) -> Attachment:
        """Rebuild from a payload produced by :meth:`to_payload` (tolerant)."""
        encoded = data.get("content")
        return cls(
            filename=str(data.get("filename") or ""),
            content=base64.b64decode(encoded) if encoded is not None else None,
            path=str(data["path"]) if data.get("path") is not None else None,
            mime=str(data["mime"]) if data.get("mime") is not None else None,
        )


@dataclass
class MailMessage:
    """One outbound email. ``from_address`` (``from`` is a keyword)."""

    subject: str
    text: str
    to: str
    html: str | None = None
    from_address: str | None = None
    from_name: str | None = None
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    reply_to: str | None = None
    attachments: list[Attachment] = field(default_factory=list)

    def attach(
        self,
        *,
        path: str | None = None,
        content: bytes | None = None,
        filename: str | None = None,
        mime: str | None = None,
    ) -> MailMessage:
        """Append one attachment — exactly one of ``path``/``content``.

        ``filename`` defaults to the path's basename; inline ``content``
        needs an explicit filename. Chainable.
        """
        if (path is None) == (content is None):
            raise ValueError("attach() needs exactly one of path= or content=")
        if content is not None and not isinstance(content, bytes):
            raise ValueError("attach() content must be bytes — encode text with .encode() first")
        resolved = filename or (Path(path).name if path else None)
        if not resolved:
            raise ValueError("attach() needs a filename for content attachments")
        self.attachments.append(
            Attachment(filename=resolved, content=content, path=path, mime=mime)
        )
        return self

    def add_cc(self, *addrs: str) -> MailMessage:
        """Append cc recipients (chainable)."""
        self.cc.extend(addrs)
        return self

    def add_bcc(self, *addrs: str) -> MailMessage:
        """Append bcc recipients (chainable)."""
        self.bcc.extend(addrs)
        return self

    def set_reply_to(self, addr: str) -> MailMessage:
        """Set the Reply-To address (chainable)."""
        self.reply_to = addr
        return self


def message_as_dict(message: MailMessage) -> dict[str, Any]:
    """The JSON-safe dict queued jobs and log lines carry (attachments base64)."""
    payload = dataclasses.asdict(message)
    # asdict leaves raw bytes on attachments — swap in the base64 payload form.
    payload["attachments"] = [attachment.to_payload() for attachment in message.attachments]
    return payload


def message_from_dict(data: dict) -> MailMessage:
    """Rebuild a MailMessage from a queued payload (str-coerced, None kept).

    Tolerant of pre-richness payloads: missing cc/bcc/reply_to/attachments
    keys read as empty/None, so old queue entries still deliver.
    """
    return MailMessage(
        subject=str(data.get("subject") or ""),
        text=str(data.get("text") or ""),
        to=str(data.get("to") or ""),
        html=str(data["html"]) if data.get("html") is not None else None,
        from_address=str(data["from_address"]) if data.get("from_address") is not None else None,
        from_name=str(data["from_name"]) if data.get("from_name") is not None else None,
        cc=[str(addr) for addr in (data.get("cc") or [])],
        bcc=[str(addr) for addr in (data.get("bcc") or [])],
        reply_to=str(data["reply_to"]) if data.get("reply_to") is not None else None,
        attachments=[Attachment.from_payload(a) for a in (data.get("attachments") or [])],
    )
