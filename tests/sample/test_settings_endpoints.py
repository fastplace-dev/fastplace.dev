"""Settings write endpoints — the targets the shipped settings UI posts to.

The settings GET pages have shipped since the auth phase; the profile,
password and delete forms post to PATCH /settings/profile,
PUT /settings/password and DELETE /settings/profile. These tests pin the
write surface: authenticated, validated, and reusing the module's services.
"""

from __future__ import annotations

import datetime

import pytest


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Fresh auth/mail state before AND after — mirrors the mail suite.

    MAIL_DRIVER=memory so the re-verification mail lands in the inspectable
    outbox; APP_KEY signs that link. The auth-state resets must bracket the
    per-test app build because the guard/passkey singletons bind caches and
    tables at construction time.
    """
    import fastplace.auth.passkey_guard as passkey_guard_module
    from fastplace.auth import passkeys as passkeys_module
    from fastplace.auth.remember import reset_remember_store
    from fastplace.cache import reset_cache
    from fastplace.events import reset_listeners
    from fastplace.mail import clear_mail_outbox
    from fastplace.queue import reset_registry

    monkeypatch.setenv("MAIL_DRIVER", "memory")
    monkeypatch.setenv("APP_KEY", "test-app-key-settings")
    reset_cache()
    reset_remember_store()
    passkeys_module.reset_passkey_store()
    passkey_guard_module._passkey_guard_instance = None
    reset_listeners()
    reset_registry()
    clear_mail_outbox()
    yield
    reset_cache()
    reset_remember_store()
    passkeys_module.reset_passkey_store()
    passkey_guard_module._passkey_guard_instance = None
    reset_listeners()
    reset_registry()
    clear_mail_outbox()


async def _make_user(email: str, *, name: str = "Settings User") -> None:
    from app.modules.accounts.models.user import User
    from fastplace.auth.hashing import Hash

    await User.create(
        name=name,
        email=email,
        password_hash=Hash.make("secret123"),
        email_verified_at=datetime.datetime.now(datetime.UTC),
    )


async def _login(client, email: str) -> None:
    response = await client.post("/login", json={"email": email, "password": "secret123"})
    assert response.status_code == 303


async def _login_settings_user(client) -> None:
    await _make_user("set@example.test")
    await _login(client, "set@example.test")


async def _find_user(email: str):
    from app.modules.accounts.repositories.user_repository import UserRepository

    return await UserRepository().find_by_email(email)


async def test_settings_writes_require_authentication(sample_client):
    """All three targets bounce anonymous traffic before any logic runs.

    These are web form targets, so an anonymous browser is redirected to
    /login (the JSON 401 contract belongs to the /api/v1 surface).
    """
    profile = await sample_client.patch(
        "/settings/profile", json={"name": "X", "email": "x@example.test"}
    )
    password = await sample_client.put(
        "/settings/password",
        json={"current_password": "a", "password": "b", "password_confirmation": "b"},
    )
    delete = await sample_client.request("DELETE", "/settings/profile", json={"password": "a"})

    for response in (profile, password, delete):
        assert response.status_code == 302
        assert response.headers["location"].endswith("/login")


async def test_settings_alias_redirects_into_the_section(sample_client):
    """/settings has no page of its own — it aliases the profile page."""
    await _login_settings_user(sample_client)

    response = await sample_client.get("/settings", headers={"X-Fastplace-Request": "true"})
    assert response.status_code == 303
    assert response.headers["location"].endswith("/settings/profile")


async def test_profile_update_renames_without_touching_verification(sample_client):
    """Same email, new name — no re-verification mail should go out."""
    from fastplace.mail import mail_outbox

    await _login_settings_user(sample_client)

    response = await sample_client.patch(
        "/settings/profile", json={"name": "Renamed", "email": "set@example.test"}
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/settings/profile")

    user = await _find_user("set@example.test")
    assert user.name == "Renamed"
    assert user.email_verified_at is not None  # unchanged email stays verified
    assert mail_outbox() == []


async def test_profile_email_change_reruns_verification(sample_client):
    """A new address is unverified until its link is clicked (like signup)."""
    from fastplace.mail import mail_outbox

    await _login_settings_user(sample_client)
    await sample_client.patch(
        "/settings/profile", json={"name": "Settings User", "email": "moved@example.test"}
    )

    user = await _find_user("moved@example.test")
    assert user is not None
    assert user.email_verified_at is None

    outbox = mail_outbox()
    assert outbox, "the re-verification mail must be sent"
    assert outbox[-1].to == "moved@example.test"


async def test_profile_rejects_another_accounts_email(sample_client):
    """Unique-ignore-self: another account's address is a 422, not a 500."""
    await _make_user("taken@example.test", name="Other")
    await _login_settings_user(sample_client)

    response = await sample_client.patch(
        "/settings/profile", json={"name": "Settings User", "email": "taken@example.test"}
    )
    assert response.status_code == 422
    assert "email" in response.json()["errors"]
    assert await _find_user("set@example.test") is not None  # own row untouched


async def test_profile_rejects_an_invalid_payload(sample_client):
    await _login_settings_user(sample_client)

    response = await sample_client.patch("/settings/profile", json={"name": "   ", "email": "nope"})
    assert response.status_code == 422
    assert set(response.json()["errors"]) >= {"name", "email"}


async def test_password_update_requires_the_current_password(sample_client):
    """A stolen session must not rotate the password without credentials."""
    await _login_settings_user(sample_client)

    response = await sample_client.put(
        "/settings/password",
        json={
            "current_password": "wrong-password",
            "password": "new-password-123",
            "password_confirmation": "new-password-123",
        },
    )
    assert response.status_code == 422
    assert "current_password" in response.json()["errors"]

    user = await _find_user("set@example.test")
    from fastplace.auth.hashing import Hash

    assert Hash.check("secret123", user.password_hash)  # unchanged


async def test_password_update_enforces_policy(sample_client):
    from app.modules.accounts.services.password_policy import min_password_length

    await _login_settings_user(sample_client)

    response = await sample_client.put(
        "/settings/password",
        json={
            "current_password": "secret123",
            "password": "short",
            "password_confirmation": "short",
        },
    )
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert "password" in errors
    assert str(min_password_length()) in " ".join(errors["password"])

    mismatch = await sample_client.put(
        "/settings/password",
        json={
            "current_password": "secret123",
            "password": "new-password-123",
            "password_confirmation": "different-entirely",
        },
    )
    assert mismatch.status_code == 422
    assert "confirmation" in " ".join(mismatch.json()["errors"]["password"])


async def test_password_update_rotates_the_credential(sample_client):
    from fastplace.auth.hashing import Hash

    await _login_settings_user(sample_client)

    response = await sample_client.put(
        "/settings/password",
        json={
            "current_password": "secret123",
            "password": "new-password-123",
            "password_confirmation": "new-password-123",
        },
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/settings/security")

    user = await _find_user("set@example.test")
    assert Hash.check("new-password-123", user.password_hash)


async def test_account_deletion_requires_the_password(sample_client):
    await _login_settings_user(sample_client)

    response = await sample_client.request(
        "DELETE", "/settings/profile", json={"password": "wrong-password"}
    )
    assert response.status_code == 422
    assert "password" in response.json()["errors"]
    assert await _find_user("set@example.test") is not None


async def test_account_deletion_is_throttled(sample_client):
    """The delete form takes a password — guessing gets the same brake the
    password form has (throttle:6,60), not unlimited attempts."""
    await _login_settings_user(sample_client)

    throttled = None
    for _ in range(7):
        throttled = await sample_client.request(
            "DELETE", "/settings/profile", json={"password": "wrong-password"}
        )
    assert throttled.status_code == 429
    assert int(throttled.headers["Retry-After"]) >= 1
    assert await _find_user("set@example.test") is not None


async def test_account_deletion_removes_the_user_and_ends_the_session(sample_client):
    await _login_settings_user(sample_client)

    response = await sample_client.request(
        "DELETE", "/settings/profile", json={"password": "secret123"}
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/")

    assert await _find_user("set@example.test") is None

    # The session died with the account — the next authed page bounces.
    follow_up = await sample_client.get(
        "/settings/profile", headers={"X-Fastplace-Request": "true"}
    )
    assert follow_up.status_code == 401
