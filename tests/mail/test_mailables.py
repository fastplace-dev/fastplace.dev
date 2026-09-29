"""Rendered mailables — templates render INTO MailMessage, never replace it.

A Mailable is code-first: ``str.format`` placeholders (no template engine —
the house rule), every value html-escaped before it touches HTML so user
data can never inject markup, unknown names fail loud, and a missing text
body derives a best-effort tag-stripped fallback. ``build()`` produces the
same MailMessage the queue boundary already serializes — templates and
placeholders never reach a queue payload.
"""

from __future__ import annotations

import pytest

from fastplace.mail import MailMessage
from fastplace.mail.mailable import Layout, Mailable

# -- placeholder rendering -----------------------------------------------------


async def test_mailable_escapes_placeholder_values_in_html() -> None:
    mailable = Mailable(
        subject="Welcome, {name}!",
        html="<h1>Hello {name}</h1>",
        placeholders={"name": "<script>alert('x')</script>"},
    )
    message = await mailable.build(to="user@example.test")
    assert message.html == "<h1>Hello &lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;</h1>"


async def test_mailable_substitutes_subject_placeholders_without_escaping() -> None:
    mailable = Mailable(
        subject="Report for {period} — {quarter}",
        html="<p>{period}</p>",
        placeholders={"period": "Q1 & Q2", "quarter": "2026"},
    )
    message = await mailable.build(to="user@example.test")
    assert message.subject == "Report for Q1 & Q2 — 2026"  # subject lines are not HTML


async def test_mailable_keeps_explicit_text_verbatim() -> None:
    mailable = Mailable(
        subject="S",
        html="<p>{name}</p>",
        text="Plain {name} body",
        placeholders={"name": "<b>x</b>"},
    )
    message = await mailable.build(to="u@example.test")
    assert message.text == "Plain <b>x</b> body"  # explicit text is never escaped, never stripped


async def test_mailable_derives_stripped_text_fallback_from_html() -> None:
    mailable = Mailable(
        subject="S",
        html="<h1>Title</h1><p>Hello {name} &amp; friends</p><p>Second</p>",
        placeholders={"name": "Kim"},
    )
    message = await mailable.build(to="u@example.test")
    assert message.text is not None
    assert "<" not in message.text
    assert "Hello Kim & friends" in message.text
    assert "Title" in message.text
    assert "Second" in message.text


async def test_mailable_unknown_html_placeholder_fails_loud() -> None:
    mailable = Mailable(subject="S", html="<p>{oops}</p>", placeholders={"name": "Kim"})
    with pytest.raises(ValueError, match="oops"):
        await mailable.build(to="u@example.test")


async def test_mailable_unknown_subject_placeholder_fails_loud() -> None:
    mailable = Mailable(subject="{oops}", html="<p>x</p>", placeholders={})
    with pytest.raises(ValueError, match="oops"):
        await mailable.build(to="u@example.test")


# -- layout --------------------------------------------------------------------


async def test_mailable_layout_wraps_body_and_escapes_its_own_slots() -> None:
    layout = Layout(
        html="<html><body>{body}<footer>{company}</footer></body></html>",
    )
    mailable = Mailable(
        subject="S",
        html="<p>Hello {name}</p>",
        placeholders={"name": "Kim", "company": "<b>ACME</b> &amp; Sons"},
    ).layout(layout)
    message = await mailable.build(to="u@example.test")
    assert message.html == (
        "<html><body><p>Hello Kim</p>"
        "<footer>&lt;b&gt;ACME&lt;/b&gt; &amp;amp; Sons</footer></body></html>"
    )


async def test_mailable_layout_with_explicit_text_keeps_it() -> None:
    layout = Layout(html="<html><body>{body}</body></html>")
    mailable = Mailable(subject="S", html="<p>x</p>", text="plain body").layout(layout)
    message = await mailable.build(to="u@example.test")
    assert message.text == "plain body"


async def test_mailable_layout_fallback_text_strips_the_wrapped_html() -> None:
    layout = Layout(html="<html><body>{body}<footer>Foot</footer></body></html>")
    mailable = Mailable(subject="S", html="<p>Content</p>").layout(layout)
    message = await mailable.build(to="u@example.test")
    assert message.text is not None
    assert "Content" in message.text
    assert "Foot" in message.text
    assert "<" not in message.text


async def test_mailable_layout_without_body_slot_fails_loud() -> None:
    # Validated at Layout construction — earliest possible point, so every
    # build over a broken layout is impossible rather than merely caught.
    with pytest.raises(ValueError, match="body"):
        Layout(html="<html><body>no slot</body></html>")


# -- envelope parity -------------------------------------------------------------


async def test_mailable_build_carries_the_full_envelope() -> None:
    mailable = (
        Mailable(subject="S", html="<p>Doc attached {name}</p>", placeholders={"name": "Kim"})
        .add_cc("cc@example.test")
        .add_bcc("bcc@example.test")
        .set_reply_to("support@example.test")
        .attach(content=b"pdf-bytes", filename="doc.pdf", mime="application/pdf")
    )
    message = await mailable.build(to="user@example.test")
    assert isinstance(message, MailMessage)
    assert message.to == "user@example.test"
    assert message.subject == "S"
    assert message.cc == ["cc@example.test"]
    assert message.bcc == ["bcc@example.test"]
    assert message.reply_to == "support@example.test"
    assert message.attachments[0].filename == "doc.pdf"
    assert message.attachments[0].content == b"pdf-bytes"


async def test_mailable_fluent_builders_return_self() -> None:
    mailable = Mailable(subject="S", html="x")
    assert mailable.add_cc("a@x.test") is mailable
    assert mailable.set_reply_to("b@x.test") is mailable
