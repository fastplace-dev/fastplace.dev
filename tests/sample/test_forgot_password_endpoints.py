"""Forgot-password endpoint — enumeration-safe responses (spec §4.10/§6)."""

from __future__ import annotations

import httpx
import pytest

from fastplace.mail import clear_mail_outbox, mail_outbox

SENT = "We have emailed your password reset link."
REGISTER_PAYLOAD = {
    "name": "Firoz",
    "email": "firoz@example.test",
    "password": "secret123",
    "password_confirmation": "secret123",
}


@pytest.fixture(autouse=True)
def _isolated_reset_state(monkeypatch):
    """Fresh throttle cache, token store, listeners, outbox before AND after.

    The per-email reset throttle keys sha1(email) in the process cache, and
    the token store caches its ensured table — both singletons must drop
    before the per-test app (and its per-test database) comes up.
    """
    from fastplace.auth.passwords import reset_token_store
    from fastplace.auth.remember import reset_remember_store
    from fastplace.cache import reset_cache
    from fastplace.events import reset_listeners
    from fastplace.queue import reset_registry

    monkeypatch.setenv("MAIL_DRIVER", "memory")
    monkeypatch.setenv("APP_KEY", "test-app-key-forgot")
    reset_cache()
    reset_remember_store()
    reset_token_store()
    reset_listeners()
    reset_registry()
    clear_mail_outbox()
    yield
    reset_cache()
    reset_remember_store()
    reset_token_store()
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


async def _register(client) -> None:
    """Create the fixture user (with the Registered mail listener loaded), log out."""
    import app.jobs.mail  # noqa: F401  (registration then mails the verify link)

    await client.get("/login")
    created = await client.post("/register", json=REGISTER_PAYLOAD)
    assert created.status_code == 303
    logged_out = await client.post("/logout")
    assert logged_out.status_code == 303
    await client.get("/login")


async def _forgot(client, email: str):
    return await client.post("/forgot-password", json={"email": email})


class TestSendResetLink:
    async def test_known_email_mails_the_reset_link(self, client):
        await _register(client)
        response = await _forgot(client, REGISTER_PAYLOAD["email"])
        assert response.status_code == 303
        assert response.headers["location"] == "/forgot-password"
        message = mail_outbox()[-1]  # [-1]: registration's verify mail came first
        assert message.to == REGISTER_PAYLOAD["email"]
        assert message.subject == "Reset your password"
        assert "/reset-password/" in message.text
        assert "email=" in message.text
        # The flash survives the redirect into the page payload.
        page = await client.get("/forgot-password", headers={"X-Fastplace-Request": "true"})
        assert page.json()["props"]["status"] == SENT

    async def test_unknown_email_answers_identically_without_mail(self, client):
        # Deliberately does NOT import app.jobs.mail — the outbox stays empty,
        # proving no mail AND the identical 303/flash answer (no 422 ever).
        await client.get("/forgot-password")  # browser flow: load the form (mints CSRF)
        response = await _forgot(client, "ghost@example.test")
        assert response.status_code == 303
        assert response.headers["location"] == "/forgot-password"
        assert mail_outbox() == []
        page = await client.get("/forgot-password", headers={"X-Fastplace-Request": "true"})
        assert page.json()["props"]["status"] == SENT

    async def test_per_email_throttle_sends_only_one_link(self, client):
        await _register(client)
        first = await _forgot(client, REGISTER_PAYLOAD["email"])
        second = await _forgot(client, REGISTER_PAYLOAD["email"])
        assert first.status_code == 303
        assert second.status_code == 303  # throttled — same answer, no signal
        sent = [m for m in mail_outbox() if m.subject == "Reset your password"]
        assert len(sent) == 1
