"""`auth:doctor` — auth stack diagnosis (roadmap spec, app plane)."""

from __future__ import annotations

import os
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
runner = CliRunner()

_STRONG_KEY = "x" * 48  # ≥32 bytes — passes the HMAC threshold


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them."""
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich console width so fix/detail hints never wrap mid-word."""
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture(autouse=True)
def _restored_config():
    """Rebind the process config singleton after each test (test_log_prune)."""
    import fastplace.config as config_module

    saved = config_module._default_config
    yield
    config_module._default_config = saved


@pytest.fixture(autouse=True)
def _reset_auth_singletons():
    """Drop process-wide engine singletons bound to dead tmp projects."""
    yield
    from fastplace.orm.manager import reset_manager

    reset_manager()


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


def _make_project(tmp_path, monkeypatch, env_text: str = "") -> object:
    """A cwd carrying the project marker + .env; chdir'd into."""
    (tmp_path / "asgi.py").write_text("# marker\n")
    if env_text:
        (tmp_path / ".env").write_text(env_text)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_auth_doctor_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "auth:doctor" in result.stdout


def test_auth_doctor_help_exits_zero():
    result = runner.invoke(cli_app, ["auth:doctor", "--help"])
    assert result.exit_code == 0
    assert "usage" in _out(result).lower()


def test_healthy_project_checks_pass(tmp_path, monkeypatch):
    """Strong key, explicit database session driver, sqlite reachable: exit 0."""
    _make_project(
        tmp_path,
        monkeypatch,
        env_text=(
            "APP_ENV=local\n"
            f"APP_KEY={_STRONG_KEY}\n"
            "SESSION_DRIVER=database\n"
            "DATABASE_URL=sqlite+aiosqlite:///./auth_probe.sqlite3\n"
        ),
    )

    result = runner.invoke(cli_app, ["auth:doctor"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "FAIL" not in out


def test_missing_app_key_in_production_fails(tmp_path, monkeypatch):
    _make_project(
        tmp_path,
        monkeypatch,
        env_text=(
            "APP_ENV=production\n"
            "APP_KEY=\n"
            "SESSION_DRIVER=database\n"
            "DATABASE_URL=sqlite+aiosqlite:///./auth_probe.sqlite3\n"
        ),
    )

    result = runner.invoke(cli_app, ["auth:doctor"])

    assert result.exit_code == 1
    out = _out(result)
    assert "APP_KEY" in out
    assert "key:generate" in out  # the exact fix command


def test_weak_app_key_warns(tmp_path, monkeypatch):
    _make_project(
        tmp_path,
        monkeypatch,
        env_text=("APP_ENV=local\nAPP_KEY=short\nSESSION_DRIVER=memory\n"),
    )

    result = runner.invoke(cli_app, ["auth:doctor"])

    assert result.exit_code == 0, _out(result)  # WARN never fails the exit
    out = _out(result)
    assert "WARN" in out


def test_memory_session_in_production_warns(tmp_path, monkeypatch):
    """Silent in-memory fallback in production: logout-everywhere destroys nothing."""
    _make_project(
        tmp_path,
        monkeypatch,
        env_text=(
            "APP_ENV=production\n"
            f"APP_KEY={_STRONG_KEY}\n"
            "SESSION_DRIVER=memory\n"
            "DATABASE_URL=sqlite+aiosqlite:///./auth_probe.sqlite3\n"
        ),
    )

    result = runner.invoke(cli_app, ["auth:doctor"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "WARN" in out
    assert "memory" in out.lower()


def test_unknown_session_driver_fails(tmp_path, monkeypatch):
    _make_project(
        tmp_path,
        monkeypatch,
        env_text=(f"APP_ENV=local\nAPP_KEY={_STRONG_KEY}\nSESSION_DRIVER=cookiejar\n"),
    )

    result = runner.invoke(cli_app, ["auth:doctor"])

    assert result.exit_code == 1
    out = _out(result)
    assert "SESSION_DRIVER" in out


def test_loose_reset_expiry_warns(tmp_path, monkeypatch):
    """A 24h reset-token lifetime is a brute-force window, not a convenience."""
    _make_project(
        tmp_path,
        monkeypatch,
        env_text=(
            "APP_ENV=local\n"
            f"APP_KEY={_STRONG_KEY}\n"
            "SESSION_DRIVER=memory\n"
            "AUTH_PASSWORD_EXPIRE=1440\n"
        ),
    )

    result = runner.invoke(cli_app, ["auth:doctor"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "WARN" in out
    assert "AUTH_PASSWORD_EXPIRE" in out
