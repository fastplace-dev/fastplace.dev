# tests/cli/test_cache_cmds.py
"""`cache:clear` and `cache:forget` CLI commands (spec #28, #29)."""

import asyncio
import os

import pytest
from typer.testing import CliRunner

from fastplace.cache import cache, reset_cache
from fastplace.cli import app as cli_app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Every test starts with an empty memory store; the singleton drops after."""
    reset_cache()
    yield
    reset_cache()


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


def test_cache_forget_removes_seeded_key():
    asyncio.run(cache().put("cli:test:greeting", "hello", ttl=60))
    result = runner.invoke(cli_app, ["cache:forget", "cli:test:greeting"])
    assert result.exit_code == 0
    assert asyncio.run(cache().get("cli:test:greeting")) is None


def test_cache_clear_empties_store():
    asyncio.run(cache().put("cli:test:a", "1", ttl=60))
    asyncio.run(cache().put("cli:test:b", "2", ttl=60))
    result = runner.invoke(cli_app, ["cache:clear"])
    assert result.exit_code == 0
    assert asyncio.run(cache().get("cli:test:a")) is None
    assert asyncio.run(cache().get("cli:test:b")) is None


def test_cache_forget_missing_key_exits_zero_with_note():
    result = runner.invoke(cli_app, ["cache:forget", "cli:test:absent"])
    assert result.exit_code == 0
    assert "not found" in result.stdout


# --- process-env hermeticity (regression pair — file order matters) -------------


def test_cache_clear_reads_the_cwd_dotenv_during_the_run(tmp_path, monkeypatch):
    """cache:clear bootstraps config from the project root like every command,
    so a .env at the cwd IS read while the command runs. This is the leak
    source the next test pins down — the read itself is by design."""
    (tmp_path / ".env").write_text("LEAK_MARKER=leaked\nAPP_KEY=\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["cache:clear"])
    assert result.exit_code == 0


def test_process_env_survives_the_previous_cache_command():
    """Regression: load_env() writes the cwd .env's keys into the REAL
    os.environ (python-dotenv mutates the process env directly — no
    monkeypatch undoes it). Without a per-test environ restore, an empty
    APP_KEY leaked here made later files' env:encrypt tests refuse with
    "APP_KEY is required". LEAK_MARKER exists only in the .env above, so
    its absence proves the leak is contained."""
    assert os.environ.get("LEAK_MARKER") is None
