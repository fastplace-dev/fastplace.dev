"""Mail fake — assertions over the memory transport's outbox."""

from __future__ import annotations

from fastplace.mail.message import MailMessage
from fastplace.mail.transports import clear_mail_outbox, mail_outbox


class FakeMail:
    """Assertion surface for mail sent during a test.

    The ``mail`` fixture pins ``MAIL_DRIVER=memory`` and clears the outbox
    around each test; the fake reads the same outbox the memory transport
    writes, so assertions see exactly what the app sent.
    """

    def messages(self) -> list[MailMessage]:
        return mail_outbox()

    def sent_count(self) -> int:
        return len(mail_outbox())

    def sent(
        self,
        to: str | None = None,
        subject_contains: str | None = None,
        body_contains: str | None = None,
    ) -> list[MailMessage]:
        """The messages matching every given filter, in send order."""
        return [
            message
            for message in mail_outbox()
            if _matches(
                message, to=to, subject_contains=subject_contains, body_contains=body_contains
            )
        ]

    def assert_sent(
        self,
        to: str | None = None,
        subject_contains: str | None = None,
        body_contains: str | None = None,
        times: int | None = None,
    ) -> list[MailMessage]:
        """Assert at least one (or exactly ``times``) matching message(s)."""
        matches = self.sent(to=to, subject_contains=subject_contains, body_contains=body_contains)
        if times is not None:
            if len(matches) != times:
                raise AssertionError(
                    f"expected {times} matching message(s), sent {len(matches)}; "
                    f"outbox: {_describe(mail_outbox())}"
                )
        elif not matches:
            raise AssertionError(
                f"no message matched (to={to!r}, subject_contains={subject_contains!r}, "
                f"body_contains={body_contains!r}); outbox: {_describe(mail_outbox())}"
            )
        return matches

    def assert_sent_to(self, to: str, times: int | None = None) -> list[MailMessage]:
        return self.assert_sent(to=to, times=times)

    def assert_not_sent(
        self,
        to: str | None = None,
        subject_contains: str | None = None,
        body_contains: str | None = None,
    ) -> None:
        matches = self.sent(to=to, subject_contains=subject_contains, body_contains=body_contains)
        if matches:
            raise AssertionError(
                f"expected no matching message, found {len(matches)}: {_describe(matches)}"
            )

    def assert_nothing_sent(self) -> None:
        if mail_outbox():
            raise AssertionError(f"outbox is not empty: {_describe(mail_outbox())}")

    def assert_sent_count(self, count: int) -> None:
        actual = self.sent_count()
        if actual != count:
            raise AssertionError(
                f"expected {count} message(s) sent, got {actual}: {_describe(mail_outbox())}"
            )


def _matches(
    message: MailMessage,
    *,
    to: str | None,
    subject_contains: str | None,
    body_contains: str | None,
) -> bool:
    if to is not None and message.to != to and to not in message.to.split(", "):
        return False
    if subject_contains is not None and subject_contains not in message.subject:
        return False
    if body_contains is not None and body_contains not in message.text:
        return False
    return True


def _describe(messages: list[MailMessage]) -> str:
    if not messages:
        return "<empty>"
    return "\n".join(f"  - to={m.to!r} subject={m.subject!r}" for m in messages)


__all__ = ["FakeMail", "clear_mail_outbox", "mail_outbox"]
