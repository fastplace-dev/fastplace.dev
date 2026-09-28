"""PAT abilities integration — real tokens through real gated routes.

The unit suite (tests/auth/test_abilities_middleware.py) pins the gate
logic on synthetic scopes; these tests mint actual PATs (hashed at rest,
resolved by the bearer edge) and run them against abilities:/ability:
routes mounted through the kernel with the sample's middleware stack —
the full chain the audit called out as untested end to end.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import httpx
import pytest

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])

REGISTER_PAYLOAD = {
    "name": "Firoz",
    "email": "pat-abilities@example.test",
    "password": "secret123",
    "password_confirmation": "secret123",
}


@pytest.fixture(autouse=True)
def _isolated_pat_state(monkeypatch: pytest.MonkeyPatch):
    """Fresh rate-limit cache, remember store, AND PAT store around each test.

    APP_KEY: the bearer edge resolves through the ``token`` guard, which
    refuses to construct without a signing secret — without it the suite
    would silently fall back to the session identity and never exercise
    the Bearer paths under test (same pin as test_pat_endpoints.py).
    """
    monkeypatch.setenv("APP_KEY", "pat-abilities-test-secret-not-for-production")
    from fastplace.auth.remember import reset_remember_store
    from fastplace.auth.tokens import reset_pat_store
    from fastplace.cache import reset_cache

    reset_cache()
    reset_remember_store()
    reset_pat_store()
    yield
    reset_cache()
    reset_remember_store()
    reset_pat_store()


@pytest.fixture()
async def gated_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The sample middleware/alias stack around ability-gated echo routes.

    Same construction as the sample_app fixture (config/app.py MIDDLEWARE +
    ROUTE_MIDDLEWARE, fresh sqlite), with the routers swapped for gated
    echoes: ALL/ANY gates at the root (browser redirect contract) and an
    ALL gate under /api/v1 (401 envelope contract). The real auth router
    rides along so the session-authenticated case logs in for real.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/gated.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")

    from app.modules.accounts.models.personal_access_token import PersonalAccessToken  # noqa: F401
    from app.modules.accounts.models.user import User  # noqa: F401
    from fastplace.db import db
    from fastplace.http import get_app
    from fastplace.http.kernel import middleware_from_config
    from fastplace.http.response import Json
    from fastplace.http.router import Router
    from routes.auth import router as auth_router

    class EchoController:
        async def index(self, request):
            return Json({"ok": True, "user_id": request.user.id})

    web = Router()
    web.get(
        "/gated-all",
        EchoController,
        "index",
        name="t.gated_all",
        middleware=["abilities:orders,posts"],
    )
    web.get(
        "/gated-any",
        EchoController,
        "index",
        name="t.gated_any",
        middleware=["ability:orders,posts"],
    )
    api = Router()
    api.get(
        "/gated",
        EchoController,
        "index",
        name="t.api_gated",
        middleware=["abilities:orders,posts"],
    )

    await db.create_all()
    middleware = middleware_from_config(Path(_PROJECT_ROOT))
    return get_app(
        routes=web,
        auth_routes=auth_router,
        api_routes=api,
        middleware=middleware,
        config={"APP_ENV": "local", "APP_DEBUG": True},
    )


@pytest.fixture()
async def client(gated_app):
    """A browser-playing client that tracks CSRF across token rotation."""
    transport = httpx.ASGITransport(app=gated_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        token: list[str | None] = [None]

        async def attach_csrf(request: httpx.Request) -> None:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and token[0]:
                request.headers.setdefault("X-Fastplace-CSRF-Token", token[0])

        async def capture_csrf(response: httpx.Response) -> None:
            fresh = response.headers.get("X-Fastplace-CSRF-Token")
            if fresh:
                token[0] = fresh

        # Any first request mints the session + CSRF token; the middleware
        # advertises it on every response, 404s included.
        await capture_csrf(await c.get("/"))
        c.event_hooks["request"].append(attach_csrf)
        c.event_hooks["response"].append(capture_csrf)
        yield c


def _auth(plaintext: str) -> dict:
    return {"Authorization": f"Bearer {plaintext}"}


async def _user_with_token(abilities: list[str]) -> str:
    """A real user plus a real PAT (hashed at rest); plaintext returned once."""
    from app.modules.accounts.models.user import User
    from fastplace.auth.hashing import Hash
    from fastplace.auth.tokens import create_token

    user = await User.create(
        name="Gated",
        email=f"pat-{abs(hash(tuple(abilities)))}@example.test",
        password_hash=Hash.make("secret123"),
        email_verified_at=datetime.datetime.now(datetime.UTC),
    )
    return await create_token(user.id, "ci", abilities=abilities)


class TestAllGate:
    async def test_pat_missing_one_ability_is_denied(self, client):
        plaintext = await _user_with_token(["orders"])
        web = await client.get("/gated-all", headers=_auth(plaintext))
        assert web.status_code == 403
        assert "ability" in web.json()["message"].lower()

        api = await client.get("/api/v1/gated", headers=_auth(plaintext))
        assert api.status_code == 403

    async def test_pat_holding_every_ability_passes(self, client):
        plaintext = await _user_with_token(["orders", "posts"])
        resp = await client.get("/gated-all", headers=_auth(plaintext))
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_wildcard_pat_passes_the_all_gate(self, client):
        plaintext = await _user_with_token(["*"])
        resp = await client.get("/gated-all", headers=_auth(plaintext))
        assert resp.status_code == 200


class TestAnyGate:
    async def test_pat_holding_one_listed_ability_passes(self, client):
        plaintext = await _user_with_token(["orders"])
        resp = await client.get("/gated-any", headers=_auth(plaintext))
        assert resp.status_code == 200

    async def test_wildcard_pat_passes_the_any_gate(self, client):
        plaintext = await _user_with_token(["*"])
        resp = await client.get("/gated-any", headers=_auth(plaintext))
        assert resp.status_code == 200


class TestSessionAndAnonymous:
    async def test_session_authentication_passes_the_gates(self, client):
        """Abilities constrain PAT bearers only (spec §4.14) — a plain
        session user walks through the same routes unconditionally."""
        from app.modules.accounts.models.user import User
        from fastplace.auth.hashing import Hash

        await User.create(
            name="Session",
            email="session@example.test",
            password_hash=Hash.make("secret123"),
            email_verified_at=datetime.datetime.now(datetime.UTC),
        )
        login = await client.post(
            "/login", json={"email": "session@example.test", "password": "secret123"}
        )
        assert login.status_code == 303

        for url in ("/gated-all", "/gated-any", "/api/v1/gated"):
            resp = await client.get(url)
            assert resp.status_code == 200, (url, resp.status_code)

    async def test_anonymous_api_request_gets_the_401_envelope(self, client):
        resp = await client.get("/api/v1/gated")
        assert resp.status_code == 401

    async def test_anonymous_browser_request_is_redirected_to_login(self, client):
        resp = await client.get("/gated-all")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"
