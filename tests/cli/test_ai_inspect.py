"""``fastplace ai:tools`` / ``ai:vectors`` — AI registry introspection (spec #30, #31)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fastplace.ai import Tool, reset_tool_registry, reset_vector_registry, vector_registry
from fastplace.cli import app as cli_app

# Rich colorizes when the environment forces color; strip codes before matching.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


@pytest.fixture
def fresh_project(tmp_path, monkeypatch):
    """A freshly scaffolded project cwd; env, config, and app.* restored after.

    ``ai:vectors`` loads the project's .env and rebinds the config registry
    to it (the config:show contract), so both are snapshotted. Its
    vector-store import also evicts foreign cached ``app.*`` modules — the
    same mid-suite eviction ``tests/ai`` performs — so the pre-test ``app.*``
    entries are parked and restored afterward (the pattern
    ``tests/cli/_isolation.py`` documents: later suites rely on cached
    project modules staying cached).
    """
    cwd_before = Path.cwd().resolve()
    env_before = dict(os.environ)
    parked = {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["new", "blog"])
    assert result.exit_code == 0, result.output

    root = tmp_path / "blog"
    monkeypatch.chdir(root)
    try:
        yield root
    finally:
        for name in [
            n for n in list(sys.modules) if n == "app" or n.startswith("app.")
        ]:
            del sys.modules[name]
        sys.modules.update(parked)
        os.environ.clear()
        os.environ.update(env_before)
        from fastplace.config import reset_config

        reset_config(cwd_before)


@pytest.fixture
def clean_tool_registry():
    reset_tool_registry()
    yield
    reset_tool_registry()


@pytest.fixture
def default_vector_registry():
    reset_vector_registry()
    yield
    reset_vector_registry()


def _run(*args):
    result = runner.invoke(cli_app, list(args))
    return result.exit_code, ANSI_RE.sub("", result.output)


def _register_dummy_tool():
    @Tool(name="inspect_dummy", description="Dummy tool for the ai:tools test")
    def inspect_dummy(query: str) -> str:
        return query


# --- ai:tools ----------------------------------------------------------------


def test_ai_tools_lists_registered_tool(fresh_project, clean_tool_registry):
    _register_dummy_tool()
    code, out = _run("ai:tools")
    assert code == 0, out
    assert "inspect_dummy" in out
    assert "Dummy tool" in out


def test_ai_tools_empty_state(fresh_project, clean_tool_registry):
    code, out = _run("ai:tools")
    assert code == 0, out
    assert "no tools registered" in out


# --- ai:vectors --------------------------------------------------------------


def test_ai_vectors_shows_registered_and_active_store(fresh_project, default_vector_registry):
    code, out = _run("ai:vectors")
    assert code == 0, out
    assert "pgvector" in out
    assert "active" in out


def test_ai_vectors_empty_state(fresh_project):
    vector_registry.clear()
    try:
        code, out = _run("ai:vectors")
    finally:
        reset_vector_registry()
    assert code == 0, out
    assert "no vector stores" in out


# --- outside-project guards --------------------------------------------------


@pytest.mark.parametrize("command", [["ai:tools"], ["ai:vectors"]])
def test_ai_commands_outside_a_project_fail_friendly(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, command)
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in ANSI_RE.sub("", result.output)
