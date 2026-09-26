"""search:query — full-text search through the registered service (roadmap B6)."""

from __future__ import annotations

import os
import re

import pytest

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from _isolation import isolate_project_state  # noqa: F401  (reset_db + module parking)
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    search:query bootstraps config via load_env(), and python-dotenv writes
    the cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after: identical
    pattern to the fixture in test_cache_cmds.py.
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _default_search_service():
    """Every test starts from the framework default; overrides drop after.

    register_search_service() swaps a process-wide singleton — without the
    reset the fake here would leak into later-collected suites
    (tests/search). Same pattern as test_ops_commands.py.
    """
    from fastplace.search import reset_search_service

    reset_search_service()
    yield
    reset_search_service()


def _out(result) -> str:
    return ANSI_RE.sub("", result.output)


# The field import is the branch's proven fixture shape (test_db_truncate.py):
# `Field` from fastplace.orm — there is no StringField in this framework.
POST_MODEL = """\
from fastplace.orm import Field, Model


class Post(Model):
    # A private table name: the repo's own sample app also maps a Post whose
    # table lands in the shared declarative metadata, and a same-named table
    # here would make this file's import collide with it (InvalidRequestError
    # inside collect_models reads as "no known models").
    __tablename__ = "search_query_posts"

    title: str = Field()
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "app" / "modules" / "blog" / "models").mkdir(parents=True)
    (tmp_path / "app" / "modules" / "blog" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "__init__.py").write_text("")
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "blog" / "models" / "post.py").write_text(POST_MODEL)
    (tmp_path / "asgi.py").write_text("app = None\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/x.db")
    return tmp_path


def test_sqlite_reports_capability_gap_with_remediation(project):
    result = runner.invoke(cli_app, ["search:query", "hello", "--model", "Post"])
    assert result.exit_code == 1
    out = _out(result)
    assert "full-text" in out
    assert "PostgreSQL" in out or "register_search_service" in out


def test_registered_fake_service_renders_results(project):
    class _FakePost:
        def __repr__(self):
            return "Post#1 (hello)"

    class _FakeService:
        # Keyword-only model/limit mirrors the real DatabaseSearchService.search
        # (fastplace/search.py) — the registry seam contract, not positional.
        async def search(self, query, *, model=None, limit=20):
            assert model.__name__ == "Post"
            assert limit == 5
            return [_FakePost()]

    from fastplace.search import register_search_service

    register_search_service(_FakeService())
    result = runner.invoke(cli_app, ["search:query", "hello", "--model", "Post", "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert "Post#1" in _out(result)


def test_missing_model_option(project):
    result = runner.invoke(cli_app, ["search:query", "hello"])
    assert result.exit_code == 1
    assert "--model is required" in _out(result)


def test_unknown_model_lists_known(project):
    result = runner.invoke(cli_app, ["search:query", "hello", "--model", "Nope"])
    assert result.exit_code == 1
    out = _out(result)
    assert "Nope" in out
    assert "Post" in out  # known-models list offered


def test_outside_project_exits_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["search:query", "hello", "--model", "Post"])
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in _out(result)
