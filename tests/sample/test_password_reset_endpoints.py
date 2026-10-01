"""Reset-password redemption — peek-then-consume, full revocation (spec §4.10)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from fastplace.mail import clear_mail_outbox, mail_outbox

REGISTER_PAYLOAD = {
    "name": "Firoz",
    "email": "firoz@example.test",
    "password": "secret123",
    "password_confirmation": "secret123",
}
NOT_FOUND = "We could not find a user with that email address."


@pytest.fixture(autouse=True)
def _isolated_reset_state(monkeypatch):
    """Fresh singletons before AND after (the Task 9 fixture + the registry).

    _register() imports app.jobs.mail, and the sample conftest purges app.*
    modules per test — without reset_registry() the @Job re-registration
    collides. APP_KEY anchors the signed registration verification mail.
    """
    from fastplace.auth.passwords import reset_token_store
    from fastplace.auth.remember import reset_remember_store
    from fastplace.auth.tokens import reset_pat_store
    from fastplace.cache import reset_cache
    from fastplace.events import reset_listeners
    from fastplace.queue import reset_registry

    monkeypatch.setenv("MAIL_DRIVER", "memory")
    monkeypatch.setenv("APP_KEY", "test-app-key-reset")
    reset_cache()
    reset_remember_store()
    reset_token_store()
    reset_pat_store()
    reset_listeners()
    reset_registry()
    clear_mail_outbox()
    yield
    reset_cache()
    reset_remember_store()
    reset_token_store()
    reset_pat_store()
    reset_listeners()
    reset_registry()
    clear_mail_outbox()


@asynccontextmanager
async def _browser(app):
    """A browser-playing client tracking CSRF across token rotation.

    Login/registration ROTATE the CSRF token; the response hook re-captures
    whatever the middleware advertises, the request hook replays the latest.
    """
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
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


@pytest.fixture()
async def client(sample_app):
    async with _browser(sample_app) as c:
        yield c


@pytest.fixture()
async def client_b(sample_app):
    """A second device — the SAME app (one fixture cache per test), its own
    cookie jar and CSRF rotation."""
    async with _browser(sample_app) as c:
        yield c


async def _register(client) -> None:
    """Create the fixture user (Registered mail listener loaded), log out."""
    import app.jobs.mail  # noqa: F401  (registration then mails the verify link)

    await client.get("/login")
    created = await client.post("/register", json=REGISTER_PAYLOAD)
    assert created.status_code == 303
    logged_out = await client.post("/logout")
    assert logged_out.status_code == 303
    await client.get("/login")


def _reset_link() -> str:
    for message in mail_outbox():
        if message.subject == "Reset your password":
            for word in message.text.split():
                if "/reset-password/" in word:
                    return word
    raise AssertionError("no reset link in the outbox")


def _token_and_email(link: str) -> tuple[str, str]:
    token = link.removeprefix("http://localhost:9000/reset-password/").split("?")[0]
    email = parse_qs(urlparse(link).query)["email"][0]
    return token, email


async def _reset(client, token: str, email: str, password: str, confirmation: str):
    return await client.post(
        "/reset-password",
        json={
            "token": token,
            "email": email,
            "password": password,
            "password_confirmation": confirmation,
        },
    )


async def _request_reset_link(client) -> tuple[str, str]:
    response = await client.post("/forgot-password", json={"email": REGISTER_PAYLOAD["email"]})
    assert response.status_code == 303
    return _token_and_email(_reset_link())


class TestResetInvalidatesThePerformingBrowser:
    async def test_reset_from_a_logged_in_browser_ends_that_session(self, client):
        # destroy_for_user kills every row for the user — but the performing
        # request's LIVE session is rewritten by the flash persist, coming
        # back authenticated on the old cookie. The reset must log its own
        # browser out too, while the one-shot flash still renders.
        await _register(client)
        token, email = await _request_reset_link(client)
        login = await client.post(
            "/login",
            json={"email": email, "password": REGISTER_PAYLOAD["password"]},
        )
        assert login.status_code == 303
        assert (await client.get("/settings/profile")).status_code == 200

        response = await _reset(client, token, email, "new-secret-123", "new-secret-123")
        assert response.status_code == 303

        profile = await client.get("/settings/profile")
        assert profile.status_code == 302  # NOT 200 — the session died with the reset

        # The flash survives the logout semantics and renders exactly once.
        page = await client.get("/login", headers={"X-Fastplace-Request": "true"})
        assert page.json()["props"]["status"] == "Your password has been reset."


class TestReset:
    async def test_happy_path_resets_the_password(self, client):
        await _register(client)
        token, email = await _request_reset_link(client)

        response = await _reset(client, token, email, "new-secret-123", "new-secret-123")
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        page = await client.get("/login", headers={"X-Fastplace-Request": "true"})
        assert page.json()["props"]["status"] == "Your password has been reset."

        old = await client.post(
            "/login", json={"email": email, "password": REGISTER_PAYLOAD["password"]}
        )
        assert old.status_code == 422
        fresh = await client.post("/login", json={"email": email, "password": "new-secret-123"})
        assert fresh.status_code == 303

    async def test_successful_reset_burns_the_token(self, client):
        await _register(client)
        token, email = await _request_reset_link(client)
        first = await _reset(client, token, email, "new-secret-123", "new-secret-123")
        assert first.status_code == 303
        again = await _reset(client, token, email, "other-secret-123", "other-secret-123")
        assert again.status_code == 422

    async def test_short_password_does_not_burn_the_token(self, client):
        await _register(client)
        token, email = await _request_reset_link(client)

        short = await _reset(client, token, email, "short", "short")
        assert short.status_code == 422
        assert "password" in short.json()["errors"]
        # The validation failure must NOT have consumed the token — the same
        # link still redeems (Review Focus #1).
        valid = await _reset(client, token, email, "new-secret-123", "new-secret-123")
        assert valid.status_code == 303

    async def test_confirmation_mismatch_is_422(self, client):
        await _register(client)
        token, email = await _request_reset_link(client)
        response = await _reset(client, token, email, "new-secret-123", "different-123")
        assert response.status_code == 422
        assert "password" in response.json()["errors"]

    async def test_unknown_email_or_wrong_token_pins_the_generic_error(self, client):
        await _register(client)
        token, email = await _request_reset_link(client)

        unknown = await _reset(
            client, token, "ghost@example.test", "new-secret-123", "new-secret-123"
        )
        assert unknown.status_code == 422
        assert unknown.json()["errors"]["email"] == [NOT_FOUND]

        wrong = await _reset(client, "bogus-token", email, "new-secret-123", "new-secret-123")
        assert wrong.status_code == 422
        assert wrong.json()["errors"]["email"] == [NOT_FOUND]

    async def test_expired_token_is_rejected(self, client, monkeypatch):
        await _register(client)
        token, email = await _request_reset_link(client)
        monkeypatch.setattr("fastplace.auth.passwords.expire_seconds", lambda: -1)
        response = await _reset(client, token, email, "new-secret-123", "new-secret-123")
        assert response.status_code == 422
        assert response.json()["errors"]["email"] == [NOT_FOUND]

    async def test_reset_revokes_other_devices(self, client, client_b):
        await _register(client)
        # Device B signs in with remember-me and reaches an auth page.
        await client_b.get("/login")
        login = await client_b.post(
            "/login",
            json={
                "email": REGISTER_PAYLOAD["email"],
                "password": REGISTER_PAYLOAD["password"],
                "remember": "on",
            },
        )
        assert login.status_code == 303
        assert (await client_b.get("/settings/profile")).status_code == 200

        # Device A (anonymous) redeems a reset link.
        token, email = await _request_reset_link(client)
        response = await _reset(client, token, email, "new-secret-123", "new-secret-123")
        assert response.status_code == 303

        # Device B's session died (destroy_for_user — no except_session_id)…
        assert (await client_b.get("/settings/profile")).status_code == 302
        # …and so did the remember token: session cookie gone, remember cookie
        # kept — it must not resurrect the session (Review Focus #4).
        remember = client_b.cookies.get("fastplace_remember")
        client_b.cookies.clear()
        client_b.cookies.set("fastplace_remember", remember)
        assert (await client_b.get("/settings/profile")).status_code == 302


class TestResetRevokesPersonalAccessTokens:
    async def test_reset_kills_every_pat_for_the_account(self, client):
        from fastplace.auth.tokens import create_token

        await _register(client)
        from app.modules.accounts.repositories.user_repository import UserRepository

        user = await UserRepository().find_by_email(REGISTER_PAYLOAD["email"])
        plaintext = await create_token(user.id, "ci-runner")
        assert (await client.get("/settings/profile")).status_code == 302  # anonymous baseline

        token, email = await _request_reset_link(client)
        response = await _reset(client, token, email, "new-secret-123", "new-secret-123")
        assert response.status_code == 303

        # The pre-reset PAT must be dead: spec §6 — password change resets
        # sessions AND tokens.
        profile = await client.get(
            "/settings/profile", headers={"Authorization": f"Bearer {plaintext}"}
        )
        assert profile.status_code == 401
