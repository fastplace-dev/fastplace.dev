# tests/cli/test_env_lint.py
"""`env:lint` .env vs .env.example audit (roadmap spec #9)."""

import os
import re

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

    env:lint bootstraps config via load_env(), and python-dotenv writes
    the cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after (verbatim
    pattern from tests/cli/test_cache_cmds.py:26-41).
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


def _make_project(tmp_path, monkeypatch, env_text, example_text="# APP_KEY=\n"):
    (tmp_path / "asgi.py").write_text("")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.py").write_text(
        'APP_ENV = "local"\nAPP_KEY = ""\nSESSION_LIFETIME = 7200\n'
    )
    (tmp_path / ".env").write_text(env_text)
    (tmp_path / ".env.example").write_text(example_text)
    monkeypatch.chdir(tmp_path)
    return tmp_path


CLEAN_ENV = "APP_ENV=local\nAPP_KEY=" + "k" * 48 + "\n"


def test_env_lint_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "env:lint" in result.stdout


def test_clean_project_passes(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, CLEAN_ENV, "# APP_ENV=\n# APP_KEY=\n# SESSION_LIFETIME=\n")
    result = runner.invoke(cli_app, ["env:lint"])
    assert result.exit_code == 0, result.stdout


def test_duplicate_active_keys_fail(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, CLEAN_ENV + "APP_ENV=production\n")
    result = runner.invoke(cli_app, ["env:lint"])
    assert result.exit_code == 1
    assert "duplicate" in _out(result)


def test_env_key_missing_from_example_warns(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, CLEAN_ENV + "CUSTOM_FLAG=1\n", "# APP_ENV=\n")
    result = runner.invoke(cli_app, ["env:lint"])
    assert result.exit_code == 0
    out = _out(result)
    assert "CUSTOM_FLAG" in out


def test_example_only_keys_do_not_warn(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, CLEAN_ENV + "VITE_PORT=5173\nQUERY_SLOW_MS=50\n", "# APP_ENV=\n")
    result = runner.invoke(cli_app, ["env:lint"])
    assert result.exit_code == 0
    out = _out(result)
    assert "VITE_PORT" not in out.replace("VITE_PORT documentation key", "")


def test_secret_looking_example_value_fails_without_printing_it(tmp_path, monkeypatch):
    leaked = "akJ3nZk2Pq8VzR4wXb7Nc1Ym5TgH9sLd2"  # 33 chars, high entropy
    _make_project(tmp_path, monkeypatch, CLEAN_ENV, f"# APP_KEY=\nSTAGING_TOKEN={leaked}\n")
    result = runner.invoke(cli_app, ["env:lint"])
    assert result.exit_code == 1
    assert leaked not in _out(result)  # the value itself NEVER printed
    assert "STAGING_TOKEN" in _out(result)


def test_commented_secret_in_crlf_example_is_flagged(tmp_path, monkeypatch):
    leaked = "akJ3nZk2Pq8VzR4wXb7Nc1Ym5TgH9sLd2"  # 33 chars, high entropy
    example = f"# APP_ENV=\r\n# STAGING_TOKEN={leaked}\r\n"  # CRLF line endings
    _make_project(tmp_path, monkeypatch, CLEAN_ENV, example)
    result = runner.invoke(cli_app, ["env:lint"])
    assert result.exit_code == 1
    assert "STAGING_TOKEN" in _out(result)
    assert leaked not in _out(result)  # the value itself NEVER printed


def test_example_value_equal_to_live_env_secret_fails(tmp_path, monkeypatch):
    secret = "live-secret-value-0123456789abcdef"  # 34 chars
    _make_project(tmp_path, monkeypatch, CLEAN_ENV + f"SERVICE_KEY={secret}\n", f"# SERVICE_KEY={secret}\n")
    result = runner.invoke(cli_app, ["env:lint"])
    assert result.exit_code == 1
    assert secret not in _out(result)


def test_low_entropy_long_value_not_flagged(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, CLEAN_ENV, "# APP_KEY=\nPAD_VALUE=" + "a" * 40 + "\n")
    result = runner.invoke(cli_app, ["env:lint"])
    assert result.exit_code == 0


def test_coercion_failure_warns(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, CLEAN_ENV + "SESSION_LIFETIME=abc\n", "# SESSION_LIFETIME=\n")
    result = runner.invoke(cli_app, ["env:lint"])
    assert result.exit_code == 0
    assert "SESSION_LIFETIME" in _out(result)


def test_production_placeholder_key_fails(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, "APP_ENV=production\nAPP_KEY=\n", "# APP_ENV=\n# APP_KEY=\n")
    result = runner.invoke(cli_app, ["env:lint"])
    assert result.exit_code == 1
    assert "production" in _out(result)


def test_fix_syncs_example_without_touching_env(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch, CLEAN_ENV + "CUSTOM_FLAG=1\n", "# APP_ENV=\n")
    env_before = (root / ".env").read_text()
    result = runner.invoke(cli_app, ["env:lint", "--fix"])
    assert result.exit_code == 0, result.stdout
    assert (root / ".env").read_text() == env_before  # .env values NEVER touched
    example = (root / ".env.example").read_text()
    assert "CUSTOM_FLAG" in example
