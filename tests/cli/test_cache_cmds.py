# tests/cli/test_cache_cmds.py
"""`cache:clear` and `cache:forget` CLI commands (spec #28, #29)."""

import asyncio

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
