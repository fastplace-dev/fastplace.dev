"""Starter passkey suite — register, list, delete, and passwordless sign-in.

The framework routes ``/user/passkeys*`` and ``/passkeys/*`` automatically
when AUTH_PASSKEYS is enabled (config/auth.py). These flows drive the real
ceremonies end-to-end through the self-contained ES256 simulator in
``tests/support/webauthn_sim.py`` — no browser, no mocking of the verifier.

Delete or extend freely: like tests/feature/test_auth_flow.py, everything
here runs against the real application over a throwaway sqlite database.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest
from support.webauthn_sim import SimulatedAuthenticator

from fastplace.mail import clear_mail_outbox, mail_outbox

pytest.importorskip(
    "webauthn",
    reason="passkeys need the 'webauthn' extra: pip install 'fastplace[webauthn]'",
)

RP_ID = "localhost"  # APP_URL host — config/auth.py derives rp_id from it
ORIGIN = "http://localhost:8000"  # APP_URL

REGISTER_PAYLOAD = {
    "name": "Firoz",
    "email": "firoz@example.com",
    "password": "secret123",
    "password_confirmation": "secret123",
}


@pytest.fixture(autouse=True)
def _fresh_passkey_singletons():
    """Every test boots a throwaway database — the framework's cached passkey
    singletons must re-resolve their engine (and re-create the table) for it."""
    import fastplace.auth.passkey_guard as guard_module
    from fastplace.auth import passkeys as passkeys_module

    passkeys_module.reset_passkey_store()
    guard_module._passkey_guard_instance = None
    yield
    passkeys_module.reset_passkey_store()
    guard_module._passkey_guard_instance = None


async def _register_verified(client) -> SimulatedAuthenticator:
    """Sign up through the app's own flow, redeem the verification link, and
    return a fresh simulated authenticator ready to register a passkey.

    Skips cleanly when the passkey routes are not mounted (AUTH_PASSKEYS
    disabled) — the rest of the suite stays meaningful without them."""
    # Registered dynamically so static tooling analyzing the framework
    # package never reaches into the app's modules from this corpus file.
    import importlib

    importlib.import_module("app.jobs.mail")  # registers the verification listener

    await client.get("/")  # mints the session + CSRF token
    response = await client.post("/register", json=REGISTER_PAYLOAD)
    assert response.status_code == 303, response.text

    message = mail_outbox()[0]
    match = re.search(r"https?://\S+/email/verify/\S+", message.text)
    assert match is not None, message.text
    split = urlsplit(match.group(0))
    verified = await client.get(f"{split.path}?{split.query}")
    assert verified.status_code == 303, verified.text
    clear_mail_outbox()

    if (await client.get("/passkeys/login/options")).status_code == 404:
        pytest.skip("passkey routes not mounted — enable AUTH_PASSKEYS")
    return SimulatedAuthenticator(rp_id=RP_ID, origin=ORIGIN)


async def _register_passkey(client, authenticator, name: str = "Test key") -> int:
    """Full registration ceremony; the Security page lists the new passkey."""
    options = (await client.get("/user/passkeys/options")).json()
    payload = authenticator.registration_response(options["challenge"])
    response = await client.post("/user/passkeys", json={"name": name, "credential": payload})
    assert response.status_code == 200, response.text

    page = await client.get("/settings/security", headers={"X-Fastplace-Request": "true"})
    passkeys = page.json()["props"]["passkeys"]
    assert [entry["name"] for entry in passkeys] == [name]
    return passkeys[0]["id"]


async def test_register_and_delete_passkey_over_http(client):
    authenticator = await _register_verified(client)

    passkey_id = await _register_passkey(client, authenticator, "Chrome on Mac")

    deleted = await client.delete(f"/user/passkeys/{passkey_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"ok": True}

    page = await client.get("/settings/security", headers={"X-Fastplace-Request": "true"})
    assert page.json()["props"]["passkeys"] == []


async def test_passkey_login_roundtrip(client):
    authenticator = await _register_verified(client)
    await _register_passkey(client, authenticator)

    await client.post("/logout")  # signed out — the dashboard gates again
    assert (await client.get("/dashboard")).status_code == 302

    options = (await client.get("/passkeys/login/options")).json()
    assert options.get("allowCredentials", []) == []
    assertion = authenticator.assertion_response(options["challenge"])
    response = await client.post("/passkeys/login", json={"credential": assertion})
    assert response.status_code == 200, response.text
    assert response.json()["redirect"] == "/dashboard"

    # The session is authenticated: the dashboard opens again.
    assert (await client.get("/dashboard")).status_code == 200
