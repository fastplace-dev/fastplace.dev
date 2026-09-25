"""Task 21 CLI — `auth:logout-everywhere`, `throttle:clear`, `search:status`.

Ops surface (spec #48–#50): incident-response session revocation, throttle
counter resets, and search-service introspection. The session store and the
rate limiter are faked with call journals for routing assertions; throttle
clearing also gets one real pass over the memory cache, and search:status is
exercised against the real service registry (default + registered override).
"""

from __future__ import annotations

import asyncio
import os
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Rich colorizes output when the environment forces color; strip codes so
# assertions match on plain text.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    auth:logout-everywhere and throttle:clear bootstrap config via
    load_env(), and python-dotenv writes the cwd .env's keys straight
    into the REAL os.environ — a mutation no monkeypatch sees or undoes.
    At the repo root that leaks the developer's own .env (a non-production
    APP_ENV made later test:db production-guard tests miss the guard).
    Snapshot before, restore after: identical pattern to the project
    fixture in test_cache_cmds.
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


# ---------------------------------------------------------------------------
# auth:logout-everywhere — every session for one user, destroyed
# ---------------------------------------------------------------------------


class FakeSessionStore:
    """Records destroy_for_user calls; mirrors the SessionStore contract."""

    def __init__(self) -> None:
        self.destroyed: list[tuple[int, str | None]] = []
        self.removed = 0  # what destroy_for_user reports back

    async def destroy_for_user(self, user_id, *, except_session_id=None):
        self.destroyed.append((user_id, except_session_id))
        return self.removed


@pytest.fixture()
def fake_sessions(monkeypatch):
    """Route session_store() to the fake (the command imports it at call time)."""
    from fastplace.http import session as session_module

    fake = FakeSessionStore()
    monkeypatch.setattr(session_module, "session_store", lambda config_get=None: fake)
    return fake


def test_logout_everywhere_destroys_users_sessions(fake_sessions):
    fake_sessions.removed = 4

    result = runner.invoke(cli_app, ["auth:logout-everywhere", "7"])

    assert result.exit_code == 0, result.output
    assert fake_sessions.destroyed == [(7, None)]  # every session, no exceptions
    plain = ANSI_RE.sub("", result.output)
    assert "4" in plain and "7" in plain  # the operator sees the blast radius


def test_logout_everywhere_with_no_active_sessions_is_not_an_error(fake_sessions):
    fake_sessions.removed = 0

    result = runner.invoke(cli_app, ["auth:logout-everywhere", "9"])

    assert result.exit_code == 0, result.output
    assert fake_sessions.destroyed == [(9, None)]
    assert "no active sessions" in ANSI_RE.sub("", result.output)


# ---------------------------------------------------------------------------
# throttle:clear — reset one rate-limit counter
# ---------------------------------------------------------------------------


class FakeRateLimiter:
    """Records clear() calls; mirrors the RateLimiter surface the command uses."""

    def __init__(self) -> None:
        self.cleared: list[str] = []

    async def clear(self, key: str) -> None:
        self.cleared.append(key)


@pytest.fixture()
def fake_limiter(monkeypatch):
    """Every RateLimiter() the command builds is the fake."""
    from fastplace import ratelimit

    fake = FakeRateLimiter()
    monkeypatch.setattr(ratelimit, "RateLimiter", lambda store=None: fake)
    return fake


def test_throttle_clear_routes_the_key_to_the_limiter(fake_limiter):
    result = runner.invoke(cli_app, ["throttle:clear", "a1b2c3-lockout"])

    assert result.exit_code == 0, result.output
    assert fake_limiter.cleared == ["a1b2c3-lockout"]  # exactly one clear, right key
    assert "a1b2c3-lockout" in ANSI_RE.sub("", result.output)


@pytest.fixture(autouse=True)
def _fresh_cache():
    """The real-counter test starts from an empty memory store; drops after."""
    from fastplace.cache import reset_cache

    reset_cache()
    yield
    reset_cache()


def test_throttle_clear_resets_a_live_counter():
    """End-to-end over the memory cache: seed, clear via the CLI, read back."""
    from fastplace.ratelimit import RateLimiter

    limiter = RateLimiter()
    asyncio.run(limiter.hit("cli:test:lockout", decay=60))
    assert asyncio.run(limiter.attempts("cli:test:lockout")) == 1  # genuinely seeded

    result = runner.invoke(cli_app, ["throttle:clear", "cli:test:lockout"])

    assert result.exit_code == 0, result.output
    assert asyncio.run(limiter.attempts("cli:test:lockout")) == 0  # the client is free


# ---------------------------------------------------------------------------
# search:status — which search service is live
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _default_search_service():
    """Every test starts from the framework default; overrides drop after."""
    from fastplace.search import reset_search_service

    reset_search_service()
    yield
    reset_search_service()


@pytest.fixture()
def marker_project(tmp_path, monkeypatch):
    """A cwd carrying the asgi.py marker — enough for the outside-project guard."""
    (tmp_path / "asgi.py").write_text("# marker — the _project_root() check\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class MeiliSearchService:
    """A registered stand-in proving search:status reads the live registry."""

    async def search(self, query, *, model=None, limit=20):
        return []


def test_search_status_prints_the_registered_service(marker_project):
    from fastplace.search import register_search_service

    register_search_service(MeiliSearchService())

    result = runner.invoke(cli_app, ["search:status"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "MeiliSearchService" in plain
    assert "DatabaseSearchService" not in plain  # the override is the live one


def test_search_status_defaults_to_the_database_service(marker_project):
    result = runner.invoke(cli_app, ["search:status"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "DatabaseSearchService" in plain


def test_search_status_outside_a_project_fails_friendly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["search:status"])

    assert result.exit_code == 1
    assert "not inside a Fastplace project" in ANSI_RE.sub("", result.output)
