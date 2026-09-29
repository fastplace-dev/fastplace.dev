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


# -- the facade accepts a Mailable ---------------------------------------------
# Rendering happens BEFORE the queue decision: payloads carry final
# html/text, never templates or placeholders.


def _welcome_mailable() -> Mailable:
    return Mailable(
        subject="Welcome, {name}!",
        html="<h1>Hello {name}</h1>",
        placeholders={"name": "Kim & <team>"},
    )


async def test_mail_send_renders_mailable_before_delivery(monkeypatch) -> None:
    import fastplace.mail as mail_module
    from fastplace.mail import clear_mail_outbox, mail_outbox

    clear_mail_outbox()
    monkeypatch.setenv("MAIL_DRIVER", "memory")
    monkeypatch.setenv("QUEUE_DRIVER", "memory")
    final = await mail_module.Mail.to("user@example.test").send(_welcome_mailable())
    assert final.to == "user@example.test"
    assert final.subject == "Welcome, Kim & <team>!"
    assert final.html == "<h1>Hello Kim &amp; &lt;team&gt;</h1>"
    stored = mail_outbox()[0]
    assert stored.html == final.html
    assert stored.text == final.text  # stripped fallback survived the envelope
    clear_mail_outbox()


async def test_mail_send_renders_mailable_before_queueing(monkeypatch) -> None:
    import sys

    import fastplace.queue as queue_module
    from fastplace.mail import Mail, message_from_dict

    monkeypatch.setenv("MAIL_DRIVER", "smtp")
    monkeypatch.setenv("QUEUE_DRIVER", "saq")

    class StubQueue:
        def __init__(self) -> None:
            self.dispatched: list[tuple] = []

        async def dispatch(self, name, **kwargs):
            self.dispatched.append((name, kwargs))

    stub = StubQueue()
    monkeypatch.setattr(queue_module, "_default_queue", stub)

    mailable = _welcome_mailable().attach(content=b"doc", filename="d.txt")
    final = await Mail.to("queued@example.test").send(mailable)

    assert stub.dispatched[0][0] == "mail_send"
    payload = stub.dispatched[0][1]["message"]
    # Final rendered content only — no template syntax, no placeholders dict.
    assert payload["html"] == "<h1>Hello Kim &amp; &lt;team&gt;</h1>"
    assert "{" not in payload["text"] and "placeholder" not in payload
    assert message_from_dict(payload) == final  # queue round-trip is byte-honest
    assert sys.modules["fastplace.mail"].mail_outbox() == []


async def test_queued_mailable_payload_delivers_rendered_html(monkeypatch) -> None:
    """The worker side: a queued mailable payload rebuilds and delivers rendered."""
    import email as email_module

    from fastplace.mail import Mail, message_from_dict

    monkeypatch.setenv("MAIL_DRIVER", "smtp")
    monkeypatch.setenv("QUEUE_DRIVER", "memory")  # worker context: deliver directly

    captured: list = []

    async def _capture(message, **kwargs):
        captured.append(message)

    # Mail.deliver resolves the transport through the facade's own binding.
    monkeypatch.setattr("fastplace.mail.transport_for", lambda: _capture)
    await Mail.deliver(
        message_from_dict(
            {
                "subject": "Welcome, Kim & <team>!",
                "html": "<h1>Hello Kim &amp; &lt;team&gt;</h1>",
                "text": "Hello Kim & <team>",
                "to": "queued@example.test",
            }
        )
    )
    assert captured[0].html == "<h1>Hello Kim &amp; &lt;team&gt;</h1>"
    assert email_module.message_from_string("") is not None  # email module sanity


# -- the notification mail channel accepts a Mailable ---------------------------


class _User:
    email = "member@example.test"
    id = 7


class _InvoicePaid:
    from fastplace.notifications import Notification

    def via(self, notifiable):
        return ["mail"]

    def to_mail(self, notifiable):
        return _welcome_mailable().add_cc("billing@example.test")


class _AsyncInvoicePaid(_InvoicePaid):
    async def to_mail(self, notifiable):
        return _welcome_mailable()


async def test_notification_to_mail_returning_mailable_sends_rendered(monkeypatch) -> None:
    from fastplace.mail import clear_mail_outbox, mail_outbox
    from fastplace.notifications.channels import MailChannel

    clear_mail_outbox()
    monkeypatch.setenv("MAIL_DRIVER", "memory")
    monkeypatch.setenv("QUEUE_DRIVER", "memory")
    result = await MailChannel().send(_User(), _InvoicePaid())
    assert result.cc == ["billing@example.test"]  # envelope carried through the Mailable
    stored = mail_outbox()[0]
    assert stored.html == "<h1>Hello Kim &amp; &lt;team&gt;</h1>"
    assert stored.subject == "Welcome, Kim & <team>!"
    clear_mail_outbox()


async def test_notification_to_mail_awaitable_mailable_also_works(monkeypatch) -> None:
    from fastplace.mail import clear_mail_outbox, mail_outbox
    from fastplace.notifications.channels import MailChannel

    clear_mail_outbox()
    monkeypatch.setenv("MAIL_DRIVER", "memory")
    monkeypatch.setenv("QUEUE_DRIVER", "memory")
    await MailChannel().send(_User(), _AsyncInvoicePaid())
    assert "Hello Kim" in (mail_outbox()[0].html or "")
    clear_mail_outbox()
