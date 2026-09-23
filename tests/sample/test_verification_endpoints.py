"""Verification endpoints — the gate, fulfill, resend (spec §4.11)."""

from __future__ import annotations

import time

import httpx
import pytest

from fastplace.auth.signing import sign
from fastplace.mail import clear_mail_outbox, mail_outbox

REGISTER_PAYLOAD = {
    "name": "Firoz",
    "email": "firoz@example.test",
    "password": "secret123",
    "password_confirmation": "secret123",
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Fresh singletons + a fixed APP_KEY so signed links verify."""
    from fastplace.auth.passwords import reset_token_store
    from fastplace.auth.remember import reset_remember_store
    from fastplace.cache import reset_cache
    from fastplace.events import reset_listeners
    from fastplace.queue import reset_registry

    monkeypatch.setenv("MAIL_DRIVER", "memory")
    monkeypatch.setenv("APP_KEY", "test-app-key-verification")
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
    """Register + auto-login (the Registered mail listener sends the link)."""
    import app.jobs.mail  # noqa: F401

    await client.get("/login")
    created = await client.post("/register", json=REGISTER_PAYLOAD)
    assert created.status_code == 303


def _verification_link() -> str:
    for message in mail_outbox():
        if message.subject == "Verify your email address":
            for word in message.text.split():
                if "/email/verify/" in word:
                    return word
    raise AssertionError("no verification link in the outbox")


def _link_path(link: str) -> str:
    return link.removeprefix("http://localhost:8000")


async def _user():
    from app.modules.accounts.models.user import User

    return await User.where(User.email == REGISTER_PAYLOAD["email"]).first()


class TestTheGate:
    async def test_unverified_user_bounces_off_the_dashboard(self, client):
        await _register(client)
        bounce = await client.get("/dashboard", follow_redirects=False)
        assert bounce.status_code == 302
        assert bounce.headers["location"] == "/email/verify"
        notice = await client.get("/email/verify", headers={"X-Fastplace-Request": "true"})
        assert notice.status_code == 200
        assert notice.json()["component"] == "Auth/VerifyEmail"

    async def test_anonymous_bounces_at_login_instead(self, client):
        response = await client.get("/dashboard", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/login"


class TestFulfill:
    async def test_the_emailed_link_verifies_and_lands_on_the_dashboard(self, client):
        await _register(client)
        path = _link_path(_verification_link())
        response = await client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard"
        page = await client.get("/dashboard", headers={"X-Fastplace-Request": "true"})
        assert page.json()["component"] == "Dashboard/Index"
        assert (await _user()).email_verified_at is not None

    async def test_replaying_the_link_is_idempotent(self, client):
        await _register(client)
        path = _link_path(_verification_link())
        first = await client.get(path, follow_redirects=False)
        second = await client.get(path, follow_redirects=False)
        assert first.status_code == 303
        assert second.status_code == 303

    async def test_tampered_signature_is_403(self, client):
        await _register(client)
        user = await _user()
        path = f"/email/verify/{user.id}/{'0' * 64}?expires={int(time.time()) + 3600}"
        assert (await client.get(path)).status_code == 403

    async def test_expired_signature_is_403(self, client):
        await _register(client)
        user = await _user()
        signature, expires = sign(f"{user.id}|{user.email}", ttl=-10)
        path = f"/email/verify/{user.id}/{signature}?expires={expires}"
        assert (await client.get(path)).status_code == 403

    async def test_other_users_signature_is_403(self, client):
        await _register(client)
        user = await _user()
        signature, expires = sign("9999|miss@other.test")
        path = f"/email/verify/{user.id}/{signature}?expires={expires}"
        assert (await client.get(path)).status_code == 403


class TestResend:
    async def test_resend_mails_and_flashes(self, client):
        await _register(client)
        before = len(mail_outbox())
        response = await client.post("/email/verification-notification")
        assert response.status_code == 303
        assert response.headers["location"] == "/email/verify"
        assert len(mail_outbox()) == before + 1
        page = await client.get("/email/verify", headers={"X-Fastplace-Request": "true"})
        assert page.json()["props"]["status"] == "verification-link-sent"

    async def test_the_seventh_resend_in_the_window_is_throttled(self, client):
        await _register(client)
        for _ in range(6):
            response = await client.post("/email/verification-notification")
            assert response.status_code == 303
        seventh = await client.post("/email/verification-notification")
        assert seventh.status_code == 429
        assert "retry-after" in seventh.headers
