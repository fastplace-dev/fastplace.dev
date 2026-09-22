"""T4.2 — session guard: signed-cookie sessions, login/logout, request.user."""

from __future__ import annotations

from types import SimpleNamespace

from fastplace.auth.guards import SessionGuard
from fastplace.auth.providers import DictUserProvider
from tests.auth.conftest import bootstrap_csrf


class TestSessionGuardUnit:
    async def test_login_stores_the_user_identifier_in_the_session(self):
        provider = DictUserProvider()
        user = SimpleNamespace(id=7, name="Firoz")
        provider.add(user)
        guard = SessionGuard(provider)
        request = SimpleNamespace(session={}, user=None)

        await guard.login(request, user)

        assert request.session[SessionGuard.SESSION_KEY] == 7

    async def test_user_resolves_through_the_provider(self):
        provider = DictUserProvider()
        user = SimpleNamespace(id=7, name="Firoz")
        provider.add(user)
        guard = SessionGuard(provider)
        request = SimpleNamespace(session={SessionGuard.SESSION_KEY: 7})

        assert await guard.user(request) is user

    async def test_user_returns_none_without_a_session_entry(self):
        guard = SessionGuard(DictUserProvider())
        request = SimpleNamespace(session={})
        assert await guard.user(request) is None

    async def test_login_clears_pre_existing_session_state(self):
        # Session-fixation defense: nothing planted in the pre-auth session
        # (an attacker-set identifier, stale data) survives login.
        provider = DictUserProvider()
        user = SimpleNamespace(id=7, name="Firoz")
        provider.add(user)
        guard = SessionGuard(provider)
        request = SimpleNamespace(
            session={SessionGuard.SESSION_KEY: 999, "cart": [1], "_token": "old"}
        )

        await guard.login(request, user)

        assert request.session[SessionGuard.SESSION_KEY] == 7
        assert "cart" not in request.session  # pre-auth state is discarded

    async def test_login_rotates_the_csrf_token(self):
        # The CSRF token must change across the privilege boundary — a token
        # observed pre-login must not authorize post-login requests.
        from fastplace.auth.middleware import CSRF_SESSION_KEY

        provider = DictUserProvider()
        user = SimpleNamespace(id=7, name="Firoz")
        provider.add(user)
        guard = SessionGuard(provider)
        request = SimpleNamespace(session={CSRF_SESSION_KEY: "pre-login-token"})

        await guard.login(request, user)

        assert request.session[CSRF_SESSION_KEY] != "pre-login-token"
        assert len(request.session[CSRF_SESSION_KEY]) >= 32

    async def test_logout_clears_the_whole_session(self):
        # The identity boundary is absolute: logout drops all session state
        # (nothing of the authenticated session survives reuse).
        guard = SessionGuard(DictUserProvider())
        request = SimpleNamespace(session={SessionGuard.SESSION_KEY: 7, "cart": [1]})

        await guard.logout(request)

        assert request.session == {}


class TestSessionGuardHttp:
    async def test_login_sets_a_signed_session_cookie(self, auth_client):
        token = await bootstrap_csrf(auth_client)
        response = await auth_client.post("/login", headers={"X-Fastplace-CSRF-Token": token})
        assert response.status_code == 200
        cookie = response.cookies.get("fastplace_session")
        assert cookie, "session cookie must be issued on login"
        # The cookie is signed (itsdangerous) — the payload is not plaintext.
        assert "Firoz" not in cookie

    async def test_login_regenerates_the_session_id(self, auth_client):
        # Session-fixation defense: the pre-login session ID must not survive
        # login — the response issues a fresh ID and the old row is destroyed.
        import httpx

        token = await bootstrap_csrf(auth_client)
        old_id = auth_client.cookies.get("fastplace_session")
        assert old_id, "pre-login request must have issued a session cookie"

        response = await auth_client.post("/login", headers={"X-Fastplace-CSRF-Token": token})

        assert response.status_code == 200
        new_id = response.cookies.get("fastplace_session")
        assert new_id and new_id != old_id

        # A session ID planted before login must authenticate nobody after it.
        transport = httpx.ASGITransport(app=auth_client._transport.app)  # type: ignore[attr-defined]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as bare:
            replayed = await bare.get("/me", headers={"Cookie": f"fastplace_session={old_id}"})
        assert replayed.json() == {"user": None}

    async def test_me_resolves_request_user_from_the_session_cookie(self, auth_client):
        await login(auth_client)
        response = await auth_client.get("/me")
        assert response.status_code == 200
        assert response.json() == {"user": "Firoz"}

    async def test_unauthenticated_me_resolves_no_user(self, auth_client):
        response = await auth_client.get("/me")
        assert response.json() == {"user": None}

    async def test_logout_clears_the_authenticated_session(self, auth_client):
        await login(auth_client)
        await post_with_csrf(auth_client, "/logout")
        response = await auth_client.get("/me")
        assert response.json() == {"user": None}

    async def test_tampered_session_cookie_authenticates_nobody(self, auth_client):
        import httpx

        login = await login_response_with_cookie(auth_client)
        raw_cookie = login.cookies["fastplace_session"] + "tampered"
        # A bare client bypasses the jar — the forged cookie goes in raw.
        transport = httpx.ASGITransport(app=auth_client._transport.app)  # type: ignore[attr-defined]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as bare:
            response = await bare.get("/me", headers={"Cookie": f"fastplace_session={raw_cookie}"})
        assert response.json() == {"user": None}

    async def test_sessions_survive_across_cookies_within_one_signed_app(self, auth_client):
        # Round-trip stability: the same app must sign and verify with one key.
        await login(auth_client)
        first = await auth_client.get("/me")
        second = await auth_client.get("/me")
        assert first.json() == second.json() == {"user": "Firoz"}


async def login(client) -> None:
    """Authenticate the test client (CSRF bootstrapped first)."""
    await login_response_with_cookie(client)


async def login_response_with_cookie(client):
    from fastplace.auth.middleware import CSRF_HEADER

    token = await bootstrap_csrf(client)
    return await client.post("/login", headers={CSRF_HEADER: token})


async def post_with_csrf(client, path: str):
    from fastplace.auth.middleware import CSRF_HEADER

    token = await bootstrap_csrf(client)
    return await client.post(path, headers={CSRF_HEADER: token})
