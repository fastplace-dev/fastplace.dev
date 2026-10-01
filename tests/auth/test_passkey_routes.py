"""HTTP route tests for the framework-owned passkey surface (plan Task 4).

The routes are mounted by ``mount_passkey_routes`` exactly the way
``create_app`` does, with the framework auth/verified/guest middleware
aliases registered like config/app.py does. Every ceremony drives the real
py-webauthn codepath through the ES256 simulator.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import fastplace.auth.passkeys as passkeys_module
from fastplace.auth.guards import SessionGuard
from fastplace.auth.middleware import (
    CSRF_HEADER,
    AuthenticateMiddleware,
    CsrfMiddleware,
    EnsureEmailVerifiedMiddleware,
    GuestMiddleware,
    ResolveUserMiddleware,
)
from fastplace.auth.providers import dict_provider
from fastplace.auth.webauthn import VerifiedMaterial, b64url_decode
from fastplace.http.kernel import get_app
from fastplace.http.router import Router
from tests.auth.webauthn_fixture import SimulatedAuthenticator

RP_ID = "localhost"
ORIGIN = "http://localhost:9000"

USER = SimpleNamespace(
    id=7,
    name="Firoz",
    email="firoz@example.test",
    email_verified_at=1700000000,
    two_factor_confirmed_at=None,
)


def make_authenticator() -> SimulatedAuthenticator:
    return SimulatedAuthenticator(rp_id=RP_ID, origin=ORIGIN)


def foreign_material() -> VerifiedMaterial:
    return VerifiedMaterial(
        credential_id="Zm9yZWlnbi1jcmVkZW50aWFs",
        public_key="cHVia2V5",
        sign_count=0,
        backup_eligible=False,
        backup_state=False,
        transports=["internal"],
        aaguid=None,
        user_verified=True,
    )


def build_app():
    """App with the passkey routes auto-mounted + test login/echo routes."""
    from fastplace.auth.passkeys_routes import mount_passkey_routes

    dict_provider.add(USER)
    session = SessionGuard(dict_provider)

    class LoginController:
        async def store(self, request):
            await session.login(request, USER)
            return {"ok": True}

    class LogoutController:
        async def store(self, request):
            await session.logout(request)
            return {"ok": True}

    class MeController:
        async def index(self, request):
            return {"user": getattr(request.user, "name", None)}

    class StampController:
        async def index(self, request):
            return {"confirmed": request.session.get("password_confirmed_at")}

    class FlashController:
        async def index(self, request):
            return {
                "flash": request.session.get("_flash"),
                "errors": request.session.get("_errors"),
            }

    router = Router()
    router.post("/login", LoginController, "store", name="test.login")
    router.post("/logout", LogoutController, "store", name="test.logout")
    router.get("/me", MeController, "index", name="test.me")
    router.get("/confirm-stamp", StampController, "index", name="test.stamp")
    router.get("/flash-dump", FlashController, "index", name="test.flash")

    merged = mount_passkey_routes(router)
    return get_app(
        routes=merged,
        middleware=[ResolveUserMiddleware(), CsrfMiddleware()],
        route_middleware={
            "auth": AuthenticateMiddleware,
            "guest": GuestMiddleware,
            "verified": EnsureEmailVerifiedMiddleware,
        },
        config={"APP_ENV": "local", "APP_KEY": "passkey-routes-test-key"},
    )


@pytest.fixture(autouse=True)
def _isolation(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Fresh SQLite + cache + singletons per case (store/guard test recipe)."""
    from fastplace.cache import reset_cache
    from fastplace.db import reset_db
    from fastplace.events import reset_listeners

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/passkey_routes.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    monkeypatch.setattr(
        "fastplace.auth.guards.provider_from_config", lambda config_get: dict_provider
    )
    reset_db()
    reset_cache()
    reset_listeners()
    passkeys_module.reset_passkey_store()
    monkeypatch.setattr("fastplace.auth.passkey_guard._passkey_guard_instance", None)
    yield
    reset_db()
    reset_cache()
    reset_listeners()
    passkeys_module.reset_passkey_store()


@pytest.fixture()
async def client():
    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def csrf_token(client: httpx.AsyncClient) -> str:
    """GET any route first (issues the session token), then return it."""
    page = await client.get("/me")
    return page.headers[CSRF_HEADER]


async def login(client: httpx.AsyncClient) -> None:
    token = await csrf_token(client)
    response = await client.post("/login", headers={CSRF_HEADER: token})
    assert response.status_code == 200, response.text


async def register_over_http(
    client: httpx.AsyncClient, authenticator: SimulatedAuthenticator, name: str = "Test key"
) -> int:
    """Full registration ceremony over HTTP; returns the new row id."""
    token = await csrf_token(client)
    options = (await client.get("/user/passkeys/options", headers={CSRF_HEADER: token})).json()
    payload = authenticator.registration_response(options["challenge"])
    response = await client.post(
        "/user/passkeys",
        json={"name": name, "credential": payload},
        headers={CSRF_HEADER: token},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    rows = await passkeys_module.passkey_store().rows_for(USER.id)
    assert len(rows) == 1
    return rows[0].id


# -- gating -------------------------------------------------------------------


async def test_options_endpoints_require_auth(client):
    anonymous = await client.get("/user/passkeys/options")
    assert anonymous.status_code == 302
    assert anonymous.headers["location"] == "/login"

    bridge = await client.get("/user/passkeys/options", headers={"X-Fastplace-Request": "true"})
    assert bridge.status_code == 401
    assert bridge.json() == {"message": "Unauthenticated."}

    anonymous_confirm = await client.get("/passkeys/confirm/options")
    assert anonymous_confirm.status_code == 302
    assert anonymous_confirm.headers["location"] == "/login"

    bridge_confirm = await client.get(
        "/passkeys/confirm/options", headers={"X-Fastplace-Request": "true"}
    )
    assert bridge_confirm.status_code == 401
    assert bridge_confirm.json() == {"message": "Unauthenticated."}


async def test_login_options_open_and_discoverable(client):
    response = await client.get("/passkeys/login/options")
    assert response.status_code == 200
    payload = response.json()
    # py-webauthn 3.x mints 64-byte challenges (2.x minted 32) — pin the floor.
    assert len(b64url_decode(payload["challenge"])) >= 32
    assert payload.get("allowCredentials", []) == []
    assert payload["rpId"] == RP_ID


# -- manage -------------------------------------------------------------------


async def test_register_and_delete_roundtrip_over_http(client):
    await login(client)
    authenticator = make_authenticator()
    await register_over_http(client, authenticator, "Chrome on Mac")

    # Browser-shaped DELETE: 303 redirect-back with a flashed status message.
    row_id = (await passkeys_module.passkey_store().rows_for(USER.id))[0].id
    token = await csrf_token(client)
    deleted = await client.delete(
        f"/user/passkeys/{row_id}", headers={CSRF_HEADER: token, "Accept": "text/html"}
    )
    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/"

    dumped = (await client.get("/flash-dump")).json()
    assert dumped["flash"] == "Passkey removed."
    assert await passkeys_module.passkey_store().rows_for(USER.id) == []


async def test_delete_bridge_client_gets_json_ok(client):
    await login(client)
    row_id = await register_over_http(client, make_authenticator())
    token = await csrf_token(client)
    deleted = await client.delete(f"/user/passkeys/{row_id}", headers={CSRF_HEADER: token})
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert await passkeys_module.passkey_store().rows_for(USER.id) == []


# -- login --------------------------------------------------------------------


async def test_login_route_returns_redirect_payload(client):
    await login(client)
    authenticator = make_authenticator()
    await register_over_http(client, authenticator)

    token = await csrf_token(client)
    await client.post("/logout", headers={CSRF_HEADER: token})

    options = (await client.get("/passkeys/login/options")).json()
    assertion = authenticator.assertion_response(options["challenge"])
    token = await csrf_token(client)
    response = await client.post(
        "/passkeys/login", json={"credential": assertion}, headers={CSRF_HEADER: token}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"redirect": "/dashboard"}

    me = await client.get("/me")
    assert me.json() == {"user": "Firoz"}


# -- confirm ------------------------------------------------------------------


async def test_confirm_route_sets_stamp_and_redirects(client):
    await login(client)
    authenticator = make_authenticator()
    await register_over_http(client, authenticator)

    options = (await client.get("/passkeys/confirm/options")).json()
    assert options.get("userVerification") in ("preferred", "required")
    assertion = authenticator.assertion_response(options["challenge"])
    token = await csrf_token(client)
    response = await client.post(
        "/passkeys/confirm", json={"credential": assertion}, headers={CSRF_HEADER: token}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"redirect": "/dashboard"}

    stamp = (await client.get("/confirm-stamp")).json()
    assert isinstance(stamp["confirmed"], int)


# -- IDOR ---------------------------------------------------------------------


async def test_delete_foreign_credential_fails_clean(client):
    await login(client)
    store = passkeys_module.passkey_store()
    foreign_id = await store.create(8, "Not mine", foreign_material())

    token = await csrf_token(client)
    response = await client.delete(f"/user/passkeys/{foreign_id}", headers={CSRF_HEADER: token})
    assert response.status_code == 422
    assert response.json()["errors"] == {"credential": ["That passkey was not found."]}

    # The foreign row survives — the owner filter never matched.
    assert await store.get_by_credential_id("Zm9yZWlnbi1jcmVkZW50aWFs") is not None


# -- missing extra ------------------------------------------------------------


async def test_missing_extra_answers_501(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastplace.auth.webauthn.webauthn_available", lambda: False)
    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Guest surface, anonymous.
        options = await client.get("/passkeys/login/options")
        assert options.status_code == 501
        assert "fastplace[webauthn]" in options.json()["message"]

        token = await csrf_token(client)
        login_post = await client.post(
            "/passkeys/login", json={"credential": {}}, headers={CSRF_HEADER: token}
        )
        assert login_post.status_code == 501

        # Authenticated surface.
        token = await csrf_token(client)
        await client.post("/login", headers={CSRF_HEADER: token})
        token = await csrf_token(client)

        assert (
            await client.get("/user/passkeys/options", headers={CSRF_HEADER: token})
        ).status_code == 501

        register = await client.post(
            "/user/passkeys",
            json={"name": "Key", "credential": {}},
            headers={CSRF_HEADER: token},
        )
        assert register.status_code == 501

        destroy = await client.delete("/user/passkeys/1", headers={CSRF_HEADER: token})
        assert destroy.status_code == 501

        confirm_options = await client.get(
            "/passkeys/confirm/options", headers={CSRF_HEADER: token}
        )
        assert confirm_options.status_code == 501

        confirm = await client.post(
            "/passkeys/confirm", json={"credential": {}}, headers={CSRF_HEADER: token}
        )
        assert confirm.status_code == 501

        # Nothing else broke.
        me = await client.get("/me")
        assert me.status_code == 200
        assert me.json() == {"user": "Firoz"}


# -- CSRF ---------------------------------------------------------------------


async def test_csrf_on_state_changing_routes(client):
    # Bridge/API client: 419 JSON envelope.
    bridge = await client.post("/passkeys/login", json={"credential": {}})
    assert bridge.status_code == 419
    assert bridge.json() == {"message": "CSRF token mismatch."}

    # Browser-shaped post: 303 redirect-back with the expiry flashed under _token.
    browser = await client.post(
        "/passkeys/login",
        content=b"credential=x",
        headers={"Accept": "text/html", "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert browser.status_code == 303

    dumped = (await client.get("/flash-dump")).json()
    assert dumped["errors"] == {"_token": ["The page has expired. Please try again."]}
