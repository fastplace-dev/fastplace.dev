"""Rendered mailables — a code-first template layer that renders INTO MailMessage.

The queue boundary serializes :class:`~fastplace.mail.message.MailMessage`
directly, so rendering happens before queueing and queue payloads carry only
final html/text — never templates or placeholders. Rendering is
``str.format_map`` over escaped values (the house rule: no template engine in
core), which buys two guarantees:

- **User data can never inject markup.** Every placeholder value is
  ``html.escape``d before it touches HTML; explicit ``text`` and the subject
  line are substituted raw (a subject line is not HTML — entities would read
  literally).
- **Template drift fails loud.** A template naming a placeholder nobody
  supplied raises ``ValueError`` at render time, not a silently-empty email.

A missing ``text`` body derives a best-effort tag-stripped fallback from the
rendered HTML (entities unescaped, block boundaries become newlines) —
documented as best-effort: pass explicit ``text`` when the plain-text wording
matters. :class:`Layout` wraps the rendered body (the ``{body}`` slot is
inserted raw — it is already escaped; the layout's own slots are escaped).
Envelope parity rides by delegation: the fluent builders hold a skeleton
MailMessage, so attachment validation and queue serialization behave exactly
like a hand-built message's.
"""

from __future__ import annotations

import dataclasses
import html as html_module
import re
from typing import Any

from fastplace.mail.message import MailMessage

_BLOCK_CLOSE_RE = re.compile(r"</(?:p|div|h[1-6]|li|tr)>", re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")


class _LoudMap(dict[str, str]):
    """A format-map whose missing key is a loud ValueError, not a silent skip."""

    def __missing__(self, key: str) -> str:
        raise ValueError(f"mailable template references unknown placeholder {key!r}")


def _escaped(placeholders: dict[str, Any]) -> dict[str, str]:
    return {name: html_module.escape(str(value)) for name, value in placeholders.items()}


def _text_fallback(rendered_html: str) -> str:
    """Best-effort plain text from rendered HTML — tags out, entities decoded."""
    text = _BR_RE.sub("\n", rendered_html)
    text = _BLOCK_CLOSE_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html_module.unescape(text)
    return _BLANK_RE.sub("\n\n", text).strip()


class Layout:
    """An email's outer HTML — a ``{body}`` slot plus escaped shared slots."""

    def __init__(self, html: str) -> None:
        if "{body}" not in html:
            raise ValueError("a Layout template needs a {body} slot for the rendered body")
        self.html = html


class Mailable:
    """A template email: render placeholders, wrap in a layout, build a MailMessage.

    ``build(to)`` is the render point — subject and html substitute
    placeholders (html values escaped, subject raw), the layout (if any)
    wraps the rendered body, and the text body is the explicit template or
    the stripped fallback. Everything envelope-shaped (cc/bcc/reply_to/
    attachments) rides a skeleton MailMessage, so validation and queue
    serialization match hand-built messages exactly.
    """

    def __init__(
        self,
        *,
        subject: str,
        html: str,
        text: str | None = None,
        placeholders: dict[str, Any] | None = None,
    ) -> None:
        self._subject_template = subject
        self._html_template = html
        self._text_template = text
        self._placeholders: dict[str, Any] = dict(placeholders or {})
        self._layout: Layout | None = None
        # Envelope skeleton — the fluent builders' validation and payload
        # identity come from MailMessage itself, not a re-implementation.
        self._envelope = MailMessage(subject="", text="", to="")

    def layout(self, layout: Layout) -> Mailable:
        """Set the outer layout (chainable)."""
        self._layout = layout
        return self

    def attach(
        self,
        *,
        path: str | None = None,
        content: bytes | None = None,
        filename: str | None = None,
        mime: str | None = None,
    ) -> Mailable:
        """Append one attachment (chainable) — MailMessage.attach validation."""
        self._envelope.attach(path=path, content=content, filename=filename, mime=mime)
        return self

    def add_cc(self, *addrs: str) -> Mailable:
        """Append cc recipients (chainable)."""
        self._envelope.add_cc(*addrs)
        return self

    def add_bcc(self, *addrs: str) -> Mailable:
        """Append bcc recipients (chainable)."""
        self._envelope.add_bcc(*addrs)
        return self

    def set_reply_to(self, addr: str) -> Mailable:
        """Set the Reply-To address (chainable)."""
        self._envelope.set_reply_to(addr)
        return self

    async def build(self, to: str) -> MailMessage:
        """Render templates and hand back the queue-ready MailMessage for ``to``."""
        subject = self._subject_template.format_map(_LoudMap(self._placeholders))
        body = self._html_template.format_map(_LoudMap(_escaped(self._placeholders)))
        if self._layout is not None:
            slots = _escaped(self._placeholders)
            slots["body"] = body  # already escaped — inserted raw
            rendered_html = self._layout.html.format_map(_LoudMap(slots))
        else:
            rendered_html = body
        text = (
            self._text_template.format_map(_LoudMap(self._placeholders))
            if self._text_template is not None
            else _text_fallback(rendered_html)
        )
        return dataclasses.replace(
            self._envelope, subject=subject, html=rendered_html, text=text, to=to
        )
