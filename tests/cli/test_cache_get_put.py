"""cache:get / cache:put — manual key reads and writes (roadmap B3/B4)."""

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
    """Confine os.environ changes to the test that caused them."""
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


def _out(result) -> str:
    return ANSI_RE.sub("", result.output)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("CACHE_DRIVER", "memory")
    from fastplace.cache import cache, reset_cache

    reset_cache()
    return cache()


# --- cache:get ---
def test_get_present_key_with_ttl(store):
    asyncio.run(store.put("greeting", "hi", ttl=60))
    result = runner.invoke(cli_app, ["cache:get", "greeting"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "hi" in out
    assert re.search(r"expires? in \d+s", out)


def test_get_present_key_without_ttl(store):
    asyncio.run(store.put("forever", "x"))
    result = runner.invoke(cli_app, ["cache:get", "forever"])
    assert result.exit_code == 0, result.output
    assert "no expiry" in _out(result)


def test_get_absent_key_exits_one(store):
    result = runner.invoke(cli_app, ["cache:get", "missing"])
    assert result.exit_code == 1
    assert "cache key 'missing' not found" in _out(result)


def test_get_json_value_renders_as_json(store):
    asyncio.run(store.put("blob", {"a": 1}))
    result = runner.invoke(cli_app, ["cache:get", "blob"])
    assert result.exit_code == 0, result.output
    assert '"a": 1' in _out(result)


# Review Focus 5 — pinned here.
def test_get_stored_json_null_reports_not_found(store):
    asyncio.run(store.put("nullkey", None))
    result = runner.invoke(cli_app, ["cache:get", "nullkey"])
    assert result.exit_code == 1
    assert "not found" in _out(result)


# --- cache:put ---
def test_put_with_ttl_then_read_back(store):
    result = runner.invoke(cli_app, ["cache:put", "temp", "42", "--ttl", "60"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "Set cache key 'temp'" in out
    assert "(ttl 60s)" in out
    assert asyncio.run(store.get("temp")) == 42
    assert asyncio.run(store.ttl("temp")) > 0


def test_put_without_ttl_never_expires(store):
    result = runner.invoke(cli_app, ["cache:put", "keep", "v"])
    assert result.exit_code == 0, result.output
    assert "ttl" not in _out(result)  # no (ttl Ns) suffix
    assert asyncio.run(store.ttl("keep")) is None  # adjudicated: omitted --ttl = never expires


def test_put_json_value_is_parsed(store):
    runner.invoke(cli_app, ["cache:put", "blob", '{"a": 1}'])
    assert asyncio.run(store.get("blob")) == {"a": 1}


def test_put_raw_string_fallback(store):
    runner.invoke(cli_app, ["cache:put", "note", "just text"])
    assert asyncio.run(store.get("note")) == "just text"


def test_put_zero_ttl_is_a_red_error(store):
    result = runner.invoke(cli_app, ["cache:put", "bad", "x", "--ttl", "0"])
    assert result.exit_code == 1
    out = _out(result)
    assert "Traceback" not in out
