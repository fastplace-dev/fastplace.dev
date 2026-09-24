"""``fastplace config:show`` — effective configuration with secret masking (spec #4)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Rich colorizes when the environment forces color; strip codes before matching.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A scaffolded project cwd.

    ``config:show`` loads the project's ``.env`` into the process
    environment and rebinds the default config registry to the project —
    snapshot and restore both so later tests see neither.
    """
    cwd_before = Path.cwd().resolve()
    env_before = dict(os.environ)

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["new", "blog"])
    assert result.exit_code == 0, result.output

    root = tmp_path / "blog"
    monkeypatch.chdir(root)
    try:
        yield root
    finally:
        os.environ.clear()
        os.environ.update(env_before)
        from fastplace.config import reset_config

        reset_config(cwd_before)


def _env_value(root: Path, name: str) -> str:
    line = next(
        line for line in (root / ".env").read_text().splitlines() if line.startswith(f"{name}=")
    )
    return line.removeprefix(f"{name}=")


def _run(*args):
    result = runner.invoke(cli_app, ["config:show", *args])
    return result.exit_code, ANSI_RE.sub("", result.output)


def test_table_lists_effective_config(project):
    code, out = _run()
    assert code == 0, out
    assert "APP_ENV" in out
    assert "local" in out


def test_table_masks_secret_values(project):
    code, out = _run()
    assert code == 0, out
    assert "APP_KEY" in out
    assert "****" in out
    # The real signing key from .env must not leak into the table.
    assert _env_value(project, "APP_KEY") not in out


def test_single_key_prints_just_the_value(project):
    code, out = _run("APP_ENV")
    assert code == 0, out
    assert out.strip() == "local"


def test_secret_env_key_is_masked(project, monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "abc123")
    code, out = _run("SOME_API_KEY")
    assert code == 0, out
    assert out.strip() == "****"
    assert "abc123" not in out


def test_unknown_key_exits_one_with_message(project):
    code, out = _run("NO_SUCH_KEY")
    assert code == 1
    assert "NO_SUCH_KEY" in out


def test_outside_a_project_fails_friendly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["config:show"])
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in ANSI_RE.sub("", result.output)


def test_mask_covers_every_pattern():
    from fastplace.cli.inspect import MASK_PATTERNS, mask

    assert MASK_PATTERNS == ("KEY", "SECRET", "PASSWORD", "TOKEN")
    assert mask("SOME_API_KEY", "abc123") == "****"
    assert mask("DB_PASSWORD", "hunter2") == "****"
    assert mask("AUTH_TOKEN", "tok") == "****"
    assert mask("CLIENT_SECRET", "s") == "****"
    assert mask("APP_ENV", "local") == "local"
    assert mask("DATABASE_URL", "sqlite+aiosqlite:///./db") == "sqlite+aiosqlite:///./db"
