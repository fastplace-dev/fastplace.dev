# tests/cli/test_env_set.py
"""`env:set` .env variable writer (roadmap spec #10)."""

import os
import re
import time

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    env:set writes KEY into the real .env and bootstraps config via
    load_env(), and python-dotenv writes the cwd .env's keys straight into
    the REAL os.environ — a mutation no monkeypatch sees or undoes.
    Snapshot before, restore after (verbatim pattern from
    tests/cli/test_cache_cmds.py:26-41).
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich table width so detail cells never wrap mid-assertion.

    The command renders through the shared global console; under CliRunner
    it falls back to 80 columns and phrases like "defaults only" split
    across cell lines. COLUMNS is read live per render, so pinning it here
    makes every table one-line-per-cell (monkeypatch restores it).
    """
    monkeypatch.setenv("COLUMNS", "200")


def _make_project(tmp_path, monkeypatch, env_text="APP_ENV=local\n", example_text="# APP_ENV=\n"):
    (tmp_path / "asgi.py").write_text("")
    (tmp_path / ".env").write_text(env_text)
    (tmp_path / ".env.example").write_text(example_text)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_env_set_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "env:set" in result.stdout


def test_new_key_appended_verbatim(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch, env_text="APP_ENV=local\n")
    result = runner.invoke(cli_app, ["env:set", "CACHE_DRIVER", "redis"])
    assert result.exit_code == 0, result.stdout
    text = (root / ".env").read_text()
    assert "APP_ENV=local" in text            # existing line untouched
    assert "CACHE_DRIVER=redis" in text       # new line present verbatim


def test_existing_key_value_replaced(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch, env_text="APP_ENV=local\nCACHE_DRIVER=memory\n")
    result = runner.invoke(cli_app, ["env:set", "CACHE_DRIVER", "redis"])
    assert result.exit_code == 0, result.stdout
    text = (root / ".env").read_text()
    assert "CACHE_DRIVER=redis" in text
    assert "CACHE_DRIVER=memory" not in text


def test_same_value_again_is_unchanged_and_preserves_mtime(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch, env_text="CACHE_DRIVER=redis\n")
    before = (root / ".env").read_text()
    mtime_before = (root / ".env").stat().st_mtime_ns
    time.sleep(0.01)
    result = runner.invoke(cli_app, ["env:set", "CACHE_DRIVER", "redis"])
    assert result.exit_code == 0, result.stdout
    assert "unchanged" in _out(result).lower()
    assert (root / ".env").read_text() == before
    assert (root / ".env").stat().st_mtime_ns == mtime_before  # no rewrite happened


def test_commented_key_activation_noticed(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch, env_text="# SESSION_LIFETIME=7200\n")
    result = runner.invoke(cli_app, ["env:set", "SESSION_LIFETIME", "3600"])
    assert result.exit_code == 0, result.stdout
    assert "ACTIVATED" in _out(result)  # the commented line was the one flipped
    assert "SESSION_LIFETIME=3600" in (root / ".env").read_text()


def test_app_key_refused_with_guidance(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch, env_text="APP_KEY=old-key\n")
    before = (root / ".env").read_text()
    result = runner.invoke(cli_app, ["env:set", "APP_KEY", "attacker-chosen-key"])
    assert result.exit_code == 1
    out = _out(result)
    assert "key:generate" in out and "--force" in out
    assert (root / ".env").read_text() == before  # refused, nothing written


def test_invalid_key_name_refused(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["env:set", "lower-case", "1"])
    assert result.exit_code == 1
    assert "UPPER_SNAKE" in _out(result) or "A-Z" in _out(result)


def test_secret_value_documented_as_empty_placeholder(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    secret = "sk-live-akJ3nZk2Pq8VzR4wXb7Nc1Ym5Tg"  # 32+ chars, high entropy
    result = runner.invoke(cli_app, ["env:set", "STAGING_TOKEN", secret])
    assert result.exit_code == 0, result.stdout
    assert secret in (root / ".env").read_text()            # value IS in .env
    example = (root / ".env.example").read_text()
    assert "STAGING_TOKEN" in example                       # key documented
    assert secret not in example                            # value NEVER copied


def test_plain_value_documented_as_empty_placeholder_too(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["env:set", "MAIL_FROM", "noreply@example.test"])
    assert result.exit_code == 0, result.stdout
    example = (root / ".env.example").read_text()
    assert "MAIL_FROM" in example
    assert "noreply@example.test" not in example  # placeholders are always empty


def test_absent_env_file_created(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    (root / ".env").unlink()
    result = runner.invoke(cli_app, ["env:set", "APP_ENV", "local"])
    assert result.exit_code == 0, result.stdout
    assert "APP_ENV=local" in (root / ".env").read_text()
