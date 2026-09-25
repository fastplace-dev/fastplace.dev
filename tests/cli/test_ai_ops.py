"""App-plane AI CLI — ai:chat, ai:tool:run, ai:tool:show, ai:agents, ai:embed."""

from __future__ import annotations

import os
import re

import pytest
from _isolation import isolate_project_state, park_project_modules  # noqa: F401
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
runner = CliRunner()


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    ai:tool:run bootstraps config via load_env(), and python-dotenv writes the
    cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after: identical
    pattern to tests/cli/test_cache_cmds.py.
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _clean_ai_state():
    """Tool/agent registries and the LLM seams reset around every test."""
    import fastplace.ai.agent as agent_module
    from fastplace.ai import reset_agent_factories, reset_tool_registry

    reset_tool_registry()
    reset_agent_factories()
    agent_module._completion_fn = agent_module._default_completion_fn
    agent_module._structured_fn = agent_module._default_structured_fn
    yield
    reset_tool_registry()
    reset_agent_factories()
    agent_module._completion_fn = agent_module._default_completion_fn
    agent_module._structured_fn = agent_module._default_structured_fn


TOOLS_MODULE = '''"""Project tools — the import_tools() walk imports every module here."""

from fastplace.ai import Tool


@Tool(description="Echo the text back")
def echo(text: str, times: int = 1) -> list:
    """Echo.

    Args:
        text: the text to echo.
        times: how many copies.
    """
    return [text] * times


@Tool(description="Count characters")
async def count(text: str) -> int:
    """Count.

    Args:
        text: the text to measure.
    """
    return len(text)
'''


@pytest.fixture()
def tools_project(tmp_path, monkeypatch):
    """A tmp project with app/ai/tools/echo.py carrying sync + async tools."""
    (tmp_path / "asgi.py").write_text("# marker — the _project_root() check\n")
    for package in ("app", "app/ai", "app/ai/tools"):
        (tmp_path / package).mkdir(parents=True, exist_ok=True)
        (tmp_path / package / "__init__.py").write_text("")
    (tmp_path / "app/ai/tools/echo.py").write_text(TOOLS_MODULE)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# ai:tool:run
# ---------------------------------------------------------------------------


def test_tool_run_executes_sync_tool_and_prints_json(tools_project, park_project_modules):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    result = runner.invoke(
        cli_app, ["ai:tool:run", "echo", "--args", '{"text": "hi", "times": 2}']
    )

    assert result.exit_code == 0, result.output
    assert '"hi"' in ANSI_RE.sub("", result.output)


def test_tool_run_awaits_async_tool(tools_project, park_project_modules):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    result = runner.invoke(cli_app, ["ai:tool:run", "count", "--args", '{"text": "hello"}'])

    assert result.exit_code == 0, result.output
    assert "5" in ANSI_RE.sub("", result.output)


def test_tool_run_missing_required_arg_is_a_field_error(tools_project, park_project_modules):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    result = runner.invoke(cli_app, ["ai:tool:run", "echo", "--args", "{}"])

    assert result.exit_code == 1
    plain = ANSI_RE.sub("", result.output)
    assert "text" in plain  # pydantic names the missing field


def test_tool_run_unknown_key_is_forbidden(tools_project, park_project_modules):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    result = runner.invoke(cli_app, ["ai:tool:run", "echo", "--args", '{"text": "x", "bogus": 1}'])

    assert result.exit_code == 1
    assert "bogus" in ANSI_RE.sub("", result.output)


def test_tool_run_unknown_tool_exits_one(tools_project, park_project_modules):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    result = runner.invoke(cli_app, ["ai:tool:run", "teapot"])

    assert result.exit_code == 1
    assert "teapot" in ANSI_RE.sub("", result.output)


def test_tool_run_invalid_json_exits_one(tools_project, park_project_modules):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    result = runner.invoke(cli_app, ["ai:tool:run", "echo", "--args", "{not json"])

    assert result.exit_code == 1
    assert "invalid --args JSON" in ANSI_RE.sub("", result.output)
