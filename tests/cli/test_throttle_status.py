"""throttle:status — read limiter state without writing (roadmap B5)."""

from __future__ import annotations

import asyncio
import os
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from tests.cli._isolation import isolate_project_state  # noqa: F401

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    cache:clear/cache:forget bootstrap config via load_env(), and
    python-dotenv writes the cwd .env's keys straight into the REAL
    os.environ — a mutation no monkeypatch sees or undoes. At the repo
    root that leaks the developer's own .env (an empty APP_KEY line made
    later files' env:encrypt tests refuse). Snapshot before, restore
    after: identical pattern to the project fixture in test_env_crypt.
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


def _out(result) -> str:
    return ANSI_RE.sub("", result.output)


@pytest.fixture
def limiter():
    from fastplace.ratelimit import RateLimiter

    return RateLimiter()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("CACHE_DRIVER", "memory")


def test_below_max_reports_attempts(env, limiter):
    asyncio.run(limiter.hit("probe"))
    asyncio.run(limiter.hit("probe"))
    result = runner.invoke(cli_app, ["throttle:status", "probe", "--max", "5"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "2/5" in out
    assert "not blocked" in out


def test_at_max_reports_blocked_with_seconds(env, limiter):
    async def _seed():
        for _ in range(61):
            await limiter.hit("flood")

    asyncio.run(_seed())
    result = runner.invoke(cli_app, ["throttle:status", "flood"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "blocked" in out
    assert re.search(r"free in \d+s", out)


def test_custom_max_overrides_default(env, limiter):
    asyncio.run(limiter.hit("probe"))
    result = runner.invoke(cli_app, ["throttle:status", "probe", "--max", "1"])
    assert result.exit_code == 0, result.output
    assert "blocked" in _out(result)
