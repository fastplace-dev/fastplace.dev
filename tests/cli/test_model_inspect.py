"""``fastplace model:list`` / ``model:show`` — ORM introspection (spec #23, #24)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from _isolation import ensure_modules
from typer.testing import CliRunner

# Imported at collection time, before any test can evict them: the module
# objects keep the repo project's model classes alive for the whole session
# (``Model.__subclasses__`` holds only weak references), and the snapshot
# below lets the repo-root fixture heal a mid-session ``sys.modules``
# eviction instead of letting ``import_all_models`` re-execute the modules
# against tables that are still registered (InvalidRequestError).
import app.modules.accounts.models  # noqa: F401
import app.modules.projects.models  # noqa: F401
from fastplace.cli import app as cli_app

# Rich colorizes when the environment forces color; strip codes before matching.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[2]

_REPO_MODEL_MODULES = {
    name: module
    for name, module in sys.modules.items()
    if re.match(r"^app\.modules\.\w+\.models(\.|$)", name)
}


@pytest.fixture
def repo_project(monkeypatch):
    """cwd at the repo root, with its model modules importable.

    Earlier test files evict cached ``app.*`` modules (the stale-eviction in
    the vector/jobs importers); re-inserting the collection-time module
    objects keeps ``import_all_models`` a cache hit instead of a re-execution
    that would collide with the still-registered tables. State as found via
    ``ensure_modules``: only what the fixture healed is popped afterward —
    modules the test's own imports pulled in (``import_all_models`` walks
    every module) stay cached for ``tests/http``, whose re-import would
    collide with their still-registered tables otherwise.
    """
    monkeypatch.chdir(REPO_ROOT)
    with ensure_modules(_REPO_MODEL_MODULES):
        yield REPO_ROOT


@pytest.fixture
def empty_project(tmp_path, monkeypatch):
    """A freshly scaffolded project with no models declared."""
    cwd_before = Path.cwd().resolve()
    env_before = dict(os.environ)

    monkeypatch.chdir(tmp_path)
    # --no-auth keeps the minimal tree these empty-state assertions expect.
    result = runner.invoke(cli_app, ["new", "blog", "--no-auth"])
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


def _run(*args):
    result = runner.invoke(cli_app, list(args))
    return result.exit_code, ANSI_RE.sub("", result.output)


def test_model_list_includes_repo_models(repo_project):
    code, out = _run("model:list")
    assert code == 0, out
    assert "User" in out
    assert "users" in out  # the table column
    assert "PersonalAccessToken" in out
    assert "Project" in out


def test_model_show_user_lists_table_and_fields(repo_project):
    code, out = _run("model:show", "User")
    assert code == 0, out
    assert "users" in out  # the declared table name
    assert "email" in out  # a declared field
    assert "VARCHAR" in out  # with its column type


def test_model_show_project_lists_relationships(repo_project):
    code, out = _run("model:show", "Project")
    assert code == 0, out
    assert "tasks" in out
    assert "Task" in out
    assert "one-to-many" in out


def test_model_show_unknown_model_exits_one_with_suggestions(repo_project):
    code, out = _run("model:show", "Nope")
    assert code == 1
    assert "Nope" in out
    assert "User" in out  # the suggestion list carries the known models


def test_model_list_empty_state_is_friendly(empty_project):
    code, out = _run("model:list")
    assert code == 0, out
    assert "no models" in out


def test_model_show_with_no_models_declared(empty_project):
    code, out = _run("model:show", "Anything")
    assert code == 1
    assert "none declared" in out


@pytest.mark.parametrize("command", [["model:list"], ["model:show", "User"]])
def test_model_commands_outside_a_project_fail_friendly(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, command)
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in ANSI_RE.sub("", result.output)


def test_collect_models_scopes_to_the_project_root(empty_project, repo_project):
    from fastplace.cli.inspect import collect_models

    empty = {cls.__name__ for cls in collect_models(empty_project)}
    repo = {cls.__name__ for cls in collect_models(repo_project)}
    assert empty == set()
    assert {"User", "PersonalAccessToken", "Project", "Task"} <= repo
