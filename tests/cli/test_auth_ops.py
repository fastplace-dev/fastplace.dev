"""App-plane auth CLI — token:list, gate:check, auth:sessions, auth:2fa-disable, auth:reset-link."""

from __future__ import annotations

import asyncio
import os
import re

import pytest
from _isolation import isolate_project_state  # noqa: F401  (autouse: db + app.* isolation)
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
runner = CliRunner()


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    token:list bootstraps config via load_env(), and python-dotenv writes the
    cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after: identical
    pattern to tests/cli/test_cache_cmds.py.
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _fresh_auth_ops_state():
    """PAT-store singleton never leaks engines between tests."""
    from fastplace.auth.tokens import reset_pat_store

    reset_pat_store()
    yield
    reset_pat_store()


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A tmp project: isolated sqlite database, no APP_ENV."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/auth-ops.db")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# token:list
# ---------------------------------------------------------------------------


def test_token_list_shows_seeded_rows_with_abilities(project):
    from fastplace.auth.tokens import pat_store

    asyncio.run(pat_store().issue(1, "laptop"))
    asyncio.run(pat_store().issue(2, "ci", abilities=["posts:read"]))

    result = runner.invoke(cli_app, ["token:list"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "Personal access tokens" in plain
    assert "laptop" in plain and "ci" in plain
    assert "posts:read" in plain  # abilities render comma-joined


def test_token_list_scopes_to_one_owner(project):
    from fastplace.auth.tokens import pat_store

    asyncio.run(pat_store().issue(1, "laptop"))
    asyncio.run(pat_store().issue(2, "ci"))

    result = runner.invoke(cli_app, ["token:list", "--user", "1"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "laptop" in plain
    assert "ci" not in plain


def test_token_list_empty_is_dim_not_an_error(project):
    result = runner.invoke(cli_app, ["token:list"])
    assert result.exit_code == 0, result.output
    assert "no personal access tokens" in ANSI_RE.sub("", result.output)


def test_token_list_empty_scoped_names_the_user(project):
    result = runner.invoke(cli_app, ["token:list", "--user", "7"])
    assert result.exit_code == 0, result.output
    assert "for user 7" in ANSI_RE.sub("", result.output)
