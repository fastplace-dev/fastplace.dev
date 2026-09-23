"""MailMessage — the JSON-safe value emails carry through queue and transports."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass


@dataclass
class MailMessage:
    """One outbound email. ``from_address`` (``from`` is a keyword)."""

    subject: str
    text: str
    to: str
    html: str | None = None
    from_address: str | None = None
    from_name: str | None = None


def message_as_dict(message: MailMessage) -> dict[str, str | None]:
    """The JSON-safe dict queued jobs and log lines carry."""
    return dataclasses.asdict(message)


def message_from_dict(data: dict) -> MailMessage:
    """Rebuild a MailMessage from a queued payload (str-coerced, None kept)."""
    return MailMessage(
        subject=str(data.get("subject") or ""),
        text=str(data.get("text") or ""),
        to=str(data.get("to") or ""),
        html=str(data["html"]) if data.get("html") is not None else None,
        from_address=str(data["from_address"]) if data.get("from_address") is not None else None,
        from_name=str(data["from_name"]) if data.get("from_name") is not None else None,
    )
