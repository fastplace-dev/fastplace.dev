"""EnsurePasswordConfirmedMiddleware — the password.confirm alias (spec §4.5/§4.12)."""

from __future__ import annotations

import time

import pytest

from fastplace.auth.middleware import INTENDED_SESSION_KEY, EnsurePasswordConfirmedMiddleware
from fastplace.errors import AuthorizationError


class FakeSession(dict):
    pass


class FakeRequest:
    def __init__(
        self, session, path="/settings/security", method="GET", headers=None, full_path=None
    ):
        self.session = session
        self.path = path
        self.method = method
        self.headers = headers or {}
        self.full_path = full_path or path
        self._r = type("R", (), {"headers": self.headers})()

    @property
    def is_bridge(self):
        return (self.headers.get("X-Fastplace-Request") or "").lower() == "true"

    def header(self, name, default=""):
        return self.headers.get(name, default)


async def _next(request):
    return "passed-through"


@pytest.fixture(autouse=True)
def _timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PASSWORD_TIMEOUT", "10800")
    from fastplace.config import reset_config

    reset_config()
    yield
    reset_config()


class TestEnsurePasswordConfirmedMiddleware:
    async def test_recent_confirmation_passes_through(self):
        session = FakeSession(password_confirmed_at=int(time.time()) - 60)
        result = await EnsurePasswordConfirmedMiddleware().handle(FakeRequest(session), _next)
        assert result == "passed-through"

    async def test_confirmation_exactly_at_the_timeout_still_passes(self):
        session = FakeSession(password_confirmed_at=int(time.time()) - 10800)
        assert (
            await EnsurePasswordConfirmedMiddleware().handle(FakeRequest(session), _next)
            == "passed-through"
        )

    async def test_one_second_past_the_timeout_redirects_browser(self):
        session = FakeSession(password_confirmed_at=int(time.time()) - 10801)
        response = await EnsurePasswordConfirmedMiddleware().handle(FakeRequest(session), _next)
        assert response.status_code == 302
        assert response.headers["location"] == "/user/confirm-password"
        assert session[INTENDED_SESSION_KEY] == "/settings/security"

    async def test_missing_timestamp_redirects_with_intended(self):
        session = FakeSession()
        response = await EnsurePasswordConfirmedMiddleware().handle(
            FakeRequest(session, full_path="/settings/security?tab=2fa"), _next
        )
        assert response.status_code == 302
        assert session[INTENDED_SESSION_KEY] == "/settings/security?tab=2fa"

    async def test_non_int_timestamp_is_treated_as_unconfirmed(self):
        session = FakeSession(password_confirmed_at="yesterday")
        response = await EnsurePasswordConfirmedMiddleware().handle(FakeRequest(session), _next)
        assert response.status_code == 302

    async def test_api_path_raises_authorization_error(self):
        session = FakeSession()
        with pytest.raises(AuthorizationError):
            await EnsurePasswordConfirmedMiddleware().handle(
                FakeRequest(session, path="/api/v1/tokens", headers={"Accept": "application/json"}),
                _next,
            )

    async def test_json_fetch_without_bridge_header_raises(self):
        # A plain fetch(Accept: application/json) is an API client (R2) —
        # the 403 envelope lets the hook's !ok branch fire instead of
        # following a 302 onto an HTML page.
        session = FakeSession()
        with pytest.raises(AuthorizationError):
            await EnsurePasswordConfirmedMiddleware().handle(
                FakeRequest(session, headers={"Accept": "application/json"}), _next
            )

    async def test_bridge_request_redirects_so_the_spa_swaps(self):
        session = FakeSession()
        response = await EnsurePasswordConfirmedMiddleware().handle(
            FakeRequest(
                session, headers={"Accept": "application/json", "X-Fastplace-Request": "true"}
            ),
            _next,
        )
        assert response.status_code == 302


class TestAliasRegistration:
    def test_route_middleware_registry_names_the_alias(self):
        from fastplace.config import config, reset_config

        reset_config()  # bind to the repo's own config (test_vectors.py idiom)
        aliases = config("ROUTE_MIDDLEWARE", {}) or {}
        assert aliases["password.confirm"] == (
            "fastplace.auth.middleware.EnsurePasswordConfirmedMiddleware"
        )
