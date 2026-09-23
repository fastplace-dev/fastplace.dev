"""T4.2 — session guard: server-side sessions, login/logout, request.user."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fastplace.auth.guards import SessionGuard
from fastplace.auth.providers import DictUserProvider
from fastplace.auth.remember import (
    REMEMBER_COOKIE_NAME,
    REMEMBER_COOKIE_SCOPE,
    VIA_REMEMBER_SCOPE,
)
from fastplace.cache import reset_cache
from fastplace.events import reset_listeners
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
    async def test_login_persists_the_user_into_the_server_side_session(self, auth_client):
        token = await bootstrap_csrf(auth_client)
        response = await auth_client.post("/login", headers={"X-Fastplace-CSRF-Token": token})
        assert response.status_code == 200
        cookie = response.cookies.get("fastplace_session")
        assert cookie, "session cookie must be issued on login"
        # The cookie carries only an opaque session ID — no user payload.
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

    async def test_sessions_survive_across_requests_within_one_app(self, auth_client):
        # Round-trip stability: the same app resolves one session across requests.
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


# --- Task 6: credential core — attempt family, remember fallback, events ----


class FakeRememberStore:
    """Dict-backed remember store — DB-free guard unit tests."""

    def __init__(self) -> None:
        self.rows: dict[int, list[str]] = {}
        self.revoked_all: list[object] = []
        self._next_id = 100

    async def issue(self, user_id) -> str:
        self._next_id += 1
        validator = f"v{self._next_id}"
        self.rows.setdefault(user_id, []).append(validator)
        return f"{self._next_id}|{validator}"

    async def consume(self, cookie):
        row_id, _, validator = cookie.partition("|")
        for user_id, validators in self.rows.items():
            if validator in validators:
                validators.remove(validator)
                fresh = await self.issue(user_id)
                return user_id, fresh
        return None

    async def revoke(self, cookie) -> None:
        _, _, validator = cookie.partition("|")
        for validators in self.rows.values():
            if validator in validators:
                validators.remove(validator)
                return

    async def revoke_all_for_user(self, user_id) -> int:
        self.revoked_all.append(user_id)
        return len(self.rows.pop(user_id, []))


class FakeSessionStore:
    """Session-store stand-in tracking destroy_for_user calls."""

    def __init__(self) -> None:
        self.destroyed: list[tuple[object, str | None]] = []

    async def destroy_for_user(self, user_id, *, except_session_id=None) -> int:
        self.destroyed.append((user_id, except_session_id))
        return 0


class _FakeSession(dict):
    """Dict session counting regenerate()/invalidate() — fixation-path pins."""

    def __init__(self) -> None:
        super().__init__()
        self.session_id = "fake-session-id"
        self.regenerated = 0
        self.invalidated = False

    def regenerate(self) -> None:
        self.regenerated += 1

    def invalidate(self) -> None:
        self.invalidated = True
        self.clear()


def make_credentials_user():
    from fastplace.auth.hashing import Hash

    return SimpleNamespace(id=7, email="firoz@example.test", password=Hash.make("secret123"))


def make_request(session=None, *, scope=None, cookies=None):
    """The SimpleNamespace stub every credential-core test uses."""
    return SimpleNamespace(
        session=session if session is not None else _FakeSession(),
        scope=scope if scope is not None else {},
        cookies=cookies if cookies is not None else {},
        user=None,
        ip="10.0.0.1",
    )


@pytest.fixture(autouse=True)
def _fresh_cache():
    # The lazy limiter lands on the process-wide memory cache — counters must
    # not bleed between tests sharing one email|ip key.
    reset_cache()
    yield
    reset_cache()


@pytest.fixture(autouse=True)
def _clean_listeners():
    reset_listeners()
    yield
    reset_listeners()


@pytest.fixture()
def fake_remember(monkeypatch):
    """Point the guard's remember_store at a dict-backed fake."""
    import fastplace.auth.guards as guards_module

    store = FakeRememberStore()
    monkeypatch.setattr(guards_module, "remember_store", lambda: store)
    return store


class TestSessionGuardCredentials:
    async def test_attempt_dispatches_attempting_and_logs_in(self, fake_remember):
        from fastplace.events import listen

        provider = DictUserProvider()
        user = make_credentials_user()
        provider.add(user)
        seen: list[tuple[str, dict]] = []
        listen("Attempting", lambda e: seen.append((e.name, dict(e.payload))))
        listen("Login", lambda e: seen.append((e.name, dict(e.payload))))

        guard = SessionGuard(provider)
        request = make_request()

        ok = await guard.attempt(request, {"email": "firoz@example.test", "password": "secret123"})

        assert ok is True
        assert request.session[SessionGuard.SESSION_KEY] == 7
        assert seen[0] == (
            "Attempting",
            {"email": "firoz@example.test", "remember": False},
        )
        assert seen[1] == (
            "Login",
            {"user_id": 7, "guard": "session", "remember": False},
        )

    async def test_attempt_failure_dispatches_failed_and_returns_false(self, fake_remember):
        from fastplace.events import listen

        provider = DictUserProvider()
        provider.add(make_credentials_user())
        failures: list[dict] = []
        listen("Failed", lambda e: failures.append(dict(e.payload)))

        guard = SessionGuard(provider)
        request = make_request()

        ok = await guard.attempt(request, {"email": "firoz@example.test", "password": "wrong-pass"})

        assert ok is False
        assert SessionGuard.SESSION_KEY not in request.session
        assert failures == [{"email": "firoz@example.test"}]

    async def test_five_failures_then_sixth_raises_throttle(self, fake_remember):
        from fastplace.errors import ThrottleRequestsError

        provider = DictUserProvider()
        provider.add(make_credentials_user())
        guard = SessionGuard(provider)

        async def attempt_with(email: str):
            return await guard.attempt(make_request(), {"email": email, "password": "wrong-pass"})

        # Mixed-case email lowercases into the same limiter key — five
        # failures lock the account for both spellings.
        for _ in range(5):
            assert await attempt_with("Firoz@Example.test") is False
        with pytest.raises(ThrottleRequestsError):
            await attempt_with("firoz@example.test")

    async def test_lockout_payload_carries_email_ip_and_retry_after(self, fake_remember):
        from fastplace.errors import ThrottleRequestsError
        from fastplace.events import listen

        provider = DictUserProvider()
        provider.add(make_credentials_user())
        guard = SessionGuard(provider)
        lockouts: list[dict] = []
        listen("Lockout", lambda e: lockouts.append(dict(e.payload)))

        for _ in range(5):
            await guard.attempt(make_request(), {"email": "firoz@example.test", "password": "nope"})
        with pytest.raises(ThrottleRequestsError):
            await guard.attempt(make_request(), {"email": "firoz@example.test", "password": "nope"})

        assert len(lockouts) == 1
        assert lockouts[0]["email"] == "firoz@example.test"
        assert lockouts[0]["ip"] == "10.0.0.1"
        assert lockouts[0]["retry_after"] >= 1

    async def test_successful_attempt_clears_the_limiter(self, fake_remember):
        import hashlib

        from fastplace.ratelimit import RateLimiter

        provider = DictUserProvider()
        provider.add(make_credentials_user())
        limiter = RateLimiter()
        guard = SessionGuard(provider, limiter=limiter)
        key = hashlib.sha1(b"firoz@example.test|10.0.0.1").hexdigest()

        for _ in range(3):
            await guard.attempt(make_request(), {"email": "firoz@example.test", "password": "nope"})
        assert await limiter.attempts(key) == 3

        ok = await guard.attempt(
            make_request(), {"email": "firoz@example.test", "password": "secret123"}
        )

        assert ok is True
        assert await limiter.attempts(key) == 0  # success forgives the count

    async def test_attempt_with_remember_issues_and_queues_cookie(self, fake_remember):
        provider = DictUserProvider()
        provider.add(make_credentials_user())
        guard = SessionGuard(provider)
        request = make_request()

        ok = await guard.attempt(
            request,
            {"email": "firoz@example.test", "password": "secret123"},
            remember=True,
        )

        assert ok is True
        assert fake_remember.rows[7], "a remember pair must be issued"
        queued = request.scope[REMEMBER_COOKIE_SCOPE]
        _, _, validator = queued.partition("|")
        assert validator in fake_remember.rows[7]

    async def test_remember_fallback_logs_in_and_flags_via_remember(self, fake_remember):
        provider = DictUserProvider()
        provider.add(make_credentials_user())
        cookie = await fake_remember.issue(7)
        guard = SessionGuard(provider)
        request = make_request(cookies={REMEMBER_COOKIE_NAME: cookie})

        user = await guard.user(request)

        assert user is not None and user.id == 7
        assert request.session[SessionGuard.SESSION_KEY] == 7
        assert request.scope[VIA_REMEMBER_SCOPE] is True
        # Rotation: a fresh cookie is queued, unlike the presented one.
        assert request.scope[REMEMBER_COOKIE_SCOPE] != cookie

    async def test_remember_fallback_regenerates_the_session(self, fake_remember):
        # Review Focus #2: the fallback IS a login — the pre-fallback session
        # id must die exactly like on form login (fixation defense).
        provider = DictUserProvider()
        provider.add(make_credentials_user())
        cookie = await fake_remember.issue(7)
        guard = SessionGuard(provider)
        request = make_request(cookies={REMEMBER_COOKIE_NAME: cookie})

        await guard.user(request)

        assert request.session.regenerated == 1

    async def test_remember_fallback_rejects_tampered_cookies(self, fake_remember):
        # Review Focus #1: malformed, unknown-selector, and wrong-validator
        # cookies authenticate nobody.
        provider = DictUserProvider()
        provider.add(make_credentials_user())
        guard = SessionGuard(provider)

        for bad in ("abc", "999|zzz", ""):
            request = make_request(cookies={REMEMBER_COOKIE_NAME: bad})
            assert await guard.user(request) is None
            assert SessionGuard.SESSION_KEY not in request.session

        cookie = await fake_remember.issue(7)
        selector, _, validator = cookie.partition("|")
        tampered = f"{selector}|{validator[:-2]}xx"
        request = make_request(cookies={REMEMBER_COOKIE_NAME: tampered})
        assert await guard.user(request) is None
        assert request.session.regenerated == 0
        assert SessionGuard.SESSION_KEY not in request.session

    async def test_logout_revokes_the_remember_cookie(self, fake_remember):
        from fastplace.events import listen

        provider = DictUserProvider()
        user = make_credentials_user()
        provider.add(user)
        guard = SessionGuard(provider)
        logouts: list[dict] = []
        listen("Logout", lambda e: logouts.append(dict(e.payload)))
        request = make_request()

        await guard.login(request, user, remember=True)
        cookie = request.scope[REMEMBER_COOKIE_SCOPE]
        assert cookie

        # The browser echoes the cookie back on the logout request.
        request.cookies = {REMEMBER_COOKIE_NAME: cookie}
        await guard.logout(request)

        assert fake_remember.rows[7] == [], "the remember pair must be revoked"
        assert request.scope[REMEMBER_COOKIE_SCOPE] is None  # clear marker queued
        assert logouts == [{"user_id": 7}]
        assert request.session.invalidated is True

    async def test_logout_other_devices_with_wrong_password_destroys_nothing(self, fake_remember):
        # Review Focus #4: a wrong password revokes nothing at all — no
        # session rows, no remember tokens.
        provider = DictUserProvider()
        provider.add(make_credentials_user())
        guard = SessionGuard(provider)
        sessions = FakeSessionStore()
        request = make_request(scope={"fastplace_session_store": sessions})
        request.session[SessionGuard.SESSION_KEY] = 7

        ok = await guard.logout_other_devices(request, "wrong-password")

        assert ok is False
        assert sessions.destroyed == []
        assert fake_remember.revoked_all == []

    async def test_logout_other_devices_revokes_sessions_and_tokens(self, fake_remember):
        provider = DictUserProvider()
        provider.add(make_credentials_user())
        guard = SessionGuard(provider)
        sessions = FakeSessionStore()
        request = make_request(scope={"fastplace_session_store": sessions})
        request.session[SessionGuard.SESSION_KEY] = 7

        ok = await guard.logout_other_devices(request, "secret123")

        assert ok is True
        # The current session is spared; every other row dies.
        assert sessions.destroyed == [(7, "fake-session-id")]
        assert fake_remember.revoked_all == [7]
        # The current device keeps working: a fresh cookie is queued.
        fresh = request.scope[REMEMBER_COOKIE_SCOPE]
        assert fresh and fresh.partition("|")[2] in fake_remember.rows[7]

    async def test_login_using_id_logs_in(self, fake_remember):
        provider = DictUserProvider()
        provider.add(make_credentials_user())
        guard = SessionGuard(provider)
        request = make_request()

        assert await guard.login_using_id(request, 7) is True
        assert request.session[SessionGuard.SESSION_KEY] == 7

        other = make_request()
        assert await guard.login_using_id(other, 999) is False
        assert SessionGuard.SESSION_KEY not in other.session

    async def test_once_validates_without_session_or_events(self, fake_remember):
        from fastplace.events import listen

        provider = DictUserProvider()
        provider.add(make_credentials_user())
        guard = SessionGuard(provider)
        seen: list[str] = []
        for name in ("Attempting", "Failed", "Login", "Lockout", "Logout"):
            listen(name, lambda e: seen.append(e.name))
        request = make_request()

        assert (
            await guard.once(request, {"email": "firoz@example.test", "password": "secret123"})
            is True
        )
        assert (
            await guard.once(request, {"email": "firoz@example.test", "password": "bad"}) is False
        )

        assert SessionGuard.SESSION_KEY not in request.session
        assert seen == []

    async def test_attempt_when_runs_the_callback(self, fake_remember):
        provider = DictUserProvider()
        provider.add(make_credentials_user())
        guard = SessionGuard(provider)
        credentials = {"email": "firoz@example.test", "password": "secret123"}

        blocked = make_request()
        assert await guard.attempt_when(blocked, credentials, lambda user: False) is False
        assert SessionGuard.SESSION_KEY not in blocked.session

        allowed = make_request()
        assert await guard.attempt_when(allowed, credentials, lambda user: True) is True
        assert allowed.session[SessionGuard.SESSION_KEY] == 7

        async def gate(user):
            return user.id == 7

        async_allowed = make_request()
        assert await guard.attempt_when(async_allowed, credentials, gate) is True
        assert async_allowed.session[SessionGuard.SESSION_KEY] == 7


# --- Task 6: two-factor interrupt — attempt() parks the challenge ------------


class TestTwoFactorInterrupt:
    async def test_confirmed_two_factor_user_does_not_complete_login(self, fake_remember):
        import datetime

        from fastplace.auth.hashing import Hash

        provider = DictUserProvider()
        two_factor_user = SimpleNamespace(
            id=11,
            email="2fa@example.test",
            password=Hash.make("secret123"),
            two_factor_confirmed_at=datetime.datetime(
                2026, 9, 1, tzinfo=datetime.timezone.utc
            ),
        )
        provider.add(two_factor_user)
        guard = SessionGuard(provider)
        request = make_request()

        ok = await guard.attempt(
            request, {"email": "2fa@example.test", "password": "secret123"}
        )

        assert ok is False
        assert request.session.get(SessionGuard.SESSION_KEY) is None  # NOT logged in
        assert request.session.get("two_factor_challenge") == 11
        assert request.session.get("two_factor_remember") in (True, False)

    async def test_unconfirmed_two_factor_secret_logs_in_normally(self, fake_remember):
        # Secret present but two_factor_confirmed_at is None → the setup is
        # incomplete; login must not strand the user at a challenge.
        from fastplace.auth.hashing import Hash

        provider = DictUserProvider()
        unconfirmed = SimpleNamespace(
            id=12,
            email="setup@example.test",
            password=Hash.make("secret123"),
            two_factor_secret="JBSWY3DPEHPK3PXP",
            two_factor_confirmed_at=None,
        )
        provider.add(unconfirmed)
        guard = SessionGuard(provider)
        request = make_request()

        ok = await guard.attempt(
            request, {"email": "setup@example.test", "password": "secret123"}
        )

        assert ok is True
        assert request.session[SessionGuard.SESSION_KEY] == 12
        assert "two_factor_challenge" not in request.session

    async def test_interrupt_clears_the_login_limiter(self, fake_remember):
        # Credentials were VALID — the attempt must not count toward lockout.
        import datetime
        import hashlib

        from fastplace.auth.hashing import Hash
        from fastplace.ratelimit import RateLimiter

        provider = DictUserProvider()
        provider.add(
            SimpleNamespace(
                id=11,
                email="2fa@example.test",
                password=Hash.make("secret123"),
                two_factor_confirmed_at=datetime.datetime(
                    2026, 9, 1, tzinfo=datetime.timezone.utc
                ),
            )
        )
        limiter = RateLimiter()
        guard = SessionGuard(provider, limiter=limiter)
        key = hashlib.sha1(b"2fa@example.test|10.0.0.1").hexdigest()

        ok = await guard.attempt(
            make_request(), {"email": "2fa@example.test", "password": "secret123"}
        )

        assert ok is False
        assert await limiter.too_many_attempts(key, 5) is False

    async def test_pending_two_factor_reads_the_session(self, fake_remember):
        import datetime

        from fastplace.auth.hashing import Hash

        provider = DictUserProvider()
        provider.add(
            SimpleNamespace(
                id=11,
                email="2fa@example.test",
                password=Hash.make("secret123"),
                two_factor_confirmed_at=datetime.datetime(
                    2026, 9, 1, tzinfo=datetime.timezone.utc
                ),
            )
        )
        guard = SessionGuard(provider)
        request = make_request()

        await guard.attempt(
            request, {"email": "2fa@example.test", "password": "secret123"}
        )

        assert guard.pending_two_factor(request) is True
        request.session.pop("two_factor_challenge")
        assert guard.pending_two_factor(request) is False

    async def test_parked_challenge_session_resolves_no_user(self, fake_remember):
        # A parked session must stay anonymous: no user_id was written, and
        # the challenge key alone must never authenticate (Review Focus #5).
        import datetime

        from fastplace.auth.hashing import Hash

        provider = DictUserProvider()
        provider.add(
            SimpleNamespace(
                id=11,
                email="2fa@example.test",
                password=Hash.make("secret123"),
                two_factor_confirmed_at=datetime.datetime(
                    2026, 9, 1, tzinfo=datetime.timezone.utc
                ),
            )
        )
        guard = SessionGuard(provider)
        request = make_request()

        await guard.attempt(
            request, {"email": "2fa@example.test", "password": "secret123"}
        )

        assert request.session.get(SessionGuard.SESSION_KEY) is None
        assert await guard.user(request) is None
