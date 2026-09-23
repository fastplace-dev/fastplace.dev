"""Registration mail — the Registered listener and the mail_send job (spec §4.7/§4.8)."""

from __future__ import annotations

import httpx
import pytest

from fastplace.events import reset_listeners
from fastplace.mail import MailMessage, clear_mail_outbox, mail_outbox, message_as_dict
from fastplace.queue import reset_registry


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Fresh caches, listeners, job registry, outbox before AND after.

    The sample conftest purges app.* modules per test, so each test's import
    of app.jobs.mail re-runs @Job and listen() — both registries must be
    empty first or the re-registration collides.
    """
    from fastplace.auth.remember import reset_remember_store
    from fastplace.cache import reset_cache

    monkeypatch.setenv("MAIL_DRIVER", "memory")
    monkeypatch.setenv("APP_KEY", "test-app-key-mail")
    reset_cache()
    reset_remember_store()
    reset_listeners()
    reset_registry()
    clear_mail_outbox()
    yield
    reset_cache()
    reset_remember_store()
    reset_listeners()
    reset_registry()
    clear_mail_outbox()


@pytest.fixture()
async def client(sample_app):
    """Browser-playing client tracking CSRF across token rotation."""
    transport = httpx.ASGITransport(app=sample_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        token: list[str | None] = [None]

        async def attach_csrf(request: httpx.Request) -> None:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and token[0]:
                request.headers.setdefault("X-Fastplace-CSRF-Token", token[0])

        async def capture_csrf(response: httpx.Response) -> None:
            fresh = response.headers.get("X-Fastplace-CSRF-Token")
            if fresh:
                token[0] = fresh

        c.event_hooks["request"].append(attach_csrf)
        c.event_hooks["response"].append(capture_csrf)
        yield c


async def test_registration_mails_the_verification_link(client):
    import app.jobs.mail  # noqa: F401  (registers the listener)

    await client.get("/login")
    response = await client.post(
        "/register",
        json={
            "name": "Firoz",
            "email": "firoz@example.test",
            "password": "secret123",
            "password_confirmation": "secret123",
        },
    )
    assert response.status_code == 303
    outbox = mail_outbox()
    assert outbox[0].to == "firoz@example.test"
    assert outbox[0].subject == "Verify your email address"
    assert "/email/verify/" in outbox[0].text


def test_mail_send_is_a_registered_job():
    import app.jobs.mail  # noqa: F401
    from fastplace.queue import jobs

    assert "mail_send" in jobs()


async def test_mail_send_delivers_directly():
    import app.jobs.mail

    clear_mail_outbox()
    message = MailMessage(subject="Hello", text="Body", to="queued@example.test")
    await app.jobs.mail.mail_send(message_as_dict(message))
    assert mail_outbox()[-1].to == "queued@example.test"


async def test_queued_mail_roundtrip_delivers():
    import app.jobs.mail  # noqa: F401
    from fastplace.queue import queue

    clear_mail_outbox()
    q = queue()
    await q.dispatch(
        "mail_send",
        message=message_as_dict(
            MailMessage(subject="Hello", text="Body", to="roundtrip@example.test")
        ),
    )
    await q.run_pending()
    assert mail_outbox()[-1].to == "roundtrip@example.test"
