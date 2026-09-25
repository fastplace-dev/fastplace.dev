"""cache:status — driver report plus a live probe (roadmap A4)."""

from __future__ import annotations

import os
import re
import sys

import pytest
from _isolation import isolate_project_state  # noqa: F401  (reset_db + module parking)
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

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


def test_memory_driver_in_testing_is_healthy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("CACHE_DRIVER", "memory")
    result = runner.invoke(cli_app, ["cache:status"])
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "memory" in out
    assert "reachable" in out


def test_database_driver_probes_sqlite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("CACHE_DRIVER", "database")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/x.db")
    result = runner.invoke(cli_app, ["cache:status"])
    assert result.exit_code == 0, result.output
    assert "database" in _out(result)


def test_redis_dead_url_is_a_finding_naming_the_host(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("CACHE_DRIVER", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    result = runner.invoke(cli_app, ["cache:status"])
    assert result.exit_code == 1
    out = _out(result)
    assert "127.0.0.1" in out


def test_unknown_driver_is_a_finding(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("CACHE_DRIVER", "carrier-pigeon")
    result = runner.invoke(cli_app, ["cache:status"])
    assert result.exit_code == 1
    assert "carrier-pigeon" in _out(result)


def test_production_memory_refusal_renders_as_sickness(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CACHE_DRIVER", "memory")
    result = runner.invoke(cli_app, ["cache:status"])
    assert result.exit_code == 1
    assert "CACHE_ALLOW_MEMORY_IN_PRODUCTION" in _out(result)


def test_redis_library_missing_names_the_extra(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("CACHE_DRIVER", "redis")
    monkeypatch.setitem(sys.modules, "redis", None)
    result = runner.invoke(cli_app, ["cache:status"])
    assert result.exit_code == 1
    assert "fastplace[redis]" in _out(result)
