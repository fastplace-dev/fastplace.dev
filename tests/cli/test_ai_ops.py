"""App-plane AI CLI — ai:chat, ai:tool:run, ai:tool:show, ai:agents, ai:embed."""

from __future__ import annotations

import os
import re
from typing import Any

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


# ---------------------------------------------------------------------------
# ai:chat — every path through the _completion_fn seam, zero network
# ---------------------------------------------------------------------------


class _Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _Message:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message):
        self.message = message
        self.finish_reason = "tool_calls" if message.tool_calls else "stop"


class _Response:
    def __init__(self, message):
        self.choices = [_Choice(message)]


class _ScriptedCompletions:
    """Returns queued responses in order, recording every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("completion seam called more times than scripted")
        return self.responses.pop(0)


@pytest.fixture()
def scripted(monkeypatch):
    def _install(*responses):
        import fastplace.ai.agent as agent_module

        seam = _ScriptedCompletions(*responses)
        monkeypatch.setattr(agent_module, "_completion_fn", seam)
        return seam

    return _install


AGENTS_MODULE = '''from fastplace.ai import Agent


def helper_agent() -> Agent:
    return Agent(model="factory-model-x")
'''


@pytest.fixture()
def agents_project(tmp_path, monkeypatch):
    (tmp_path / "asgi.py").write_text("# marker — the _project_root() check\n")
    for package in ("app", "app/ai", "app/ai/agents"):
        (tmp_path / package).mkdir(parents=True, exist_ok=True)
        (tmp_path / package / "__init__.py").write_text("")
    (tmp_path / "app/ai/agents/helper.py").write_text(AGENTS_MODULE)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_chat_prints_the_completion_reply(tmp_path, monkeypatch, scripted):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    scripted(_Response(_Message(content="hello back")))

    result = runner.invoke(cli_app, ["ai:chat", "hi"])

    assert result.exit_code == 0, result.output
    assert "hello back" in ANSI_RE.sub("", result.output)


def test_chat_uses_the_factory_agent_when_named(agents_project, park_project_modules, scripted):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    scripted(_Response(_Message(content="from factory")))

    result = runner.invoke(cli_app, ["ai:chat", "hi", "--agent", "helper_agent"])

    assert result.exit_code == 0, result.output
    assert "from factory" in ANSI_RE.sub("", result.output)


def test_chat_agent_flag_needs_a_project(tmp_path, monkeypatch, scripted):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)  # no asgi.py — bare chat works, --agent must not
    scripted(_Response(_Message(content="unused")))

    result = runner.invoke(cli_app, ["ai:chat", "hi", "--agent", "helper_agent"])

    assert result.exit_code == 1
    assert "not inside a Fastplace project" in ANSI_RE.sub("", result.output)


def test_chat_unknown_agent_lists_known_factories(agents_project, park_project_modules, scripted):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    scripted(_Response(_Message(content="unused")))

    result = runner.invoke(cli_app, ["ai:chat", "hi", "--agent", "nope"])

    assert result.exit_code == 1
    plain = ANSI_RE.sub("", result.output)
    assert "nope" in plain and "helper_agent" in plain


def test_chat_model_overrides_the_agent_model(agents_project, park_project_modules, scripted):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    seam = scripted(_Response(_Message(content="ok")))

    result = runner.invoke(
        cli_app, ["ai:chat", "hi", "--agent", "helper_agent", "--model", "gpt-override"]
    )

    assert result.exit_code == 0, result.output
    assert seam.calls[0]["model"] == "gpt-override"


def test_chat_provider_failure_exits_one(tmp_path, monkeypatch, scripted):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)

    class _Boom:
        async def __call__(self, **kwargs):
            raise RuntimeError("provider down")

    import fastplace.ai.agent as agent_module

    monkeypatch.setattr(agent_module, "_completion_fn", _Boom())

    result = runner.invoke(cli_app, ["ai:chat", "hi"])

    assert result.exit_code == 1
    assert "provider down" in ANSI_RE.sub("", result.output)


class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _ChunkChoice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, content):
        self.choices = [_ChunkChoice(_Delta(content=content))]


class _StreamingCompletions:
    """stream=True calls get an async iterator of chunks."""

    def __init__(self, *chunks):
        self.chunks = list(chunks)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any):
        self.calls.append(kwargs)

        async def _aiter():
            for chunk in self.chunks:
                yield chunk

        return _aiter()


def test_chat_stream_prints_deltas_then_done(tmp_path, monkeypatch):
    import fastplace.ai.agent as agent_module

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        agent_module,
        "_completion_fn",
        _StreamingCompletions(_Chunk("hel"), _Chunk("lo")),
    )

    result = runner.invoke(cli_app, ["ai:chat", "hi", "--stream"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "hello" in plain  # both delta tokens printed inline
    assert "done" in plain


# ---------------------------------------------------------------------------
# ai:tool:show
# ---------------------------------------------------------------------------


def test_tool_show_prints_schema_and_validation_fields(tools_project, park_project_modules):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    result = runner.invoke(cli_app, ["ai:tool:show", "echo"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    # provider wire schema (pretty JSON from spec.to_openai())
    assert '"function"' in plain and '"echo"' in plain
    assert "Echo the text back" in plain  # description rides the schema
    # validation model fields
    assert "required" in plain and "int" in plain  # column header + times' type


def test_tool_show_unknown_tool_exits_one(tools_project, park_project_modules):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    result = runner.invoke(cli_app, ["ai:tool:show", "teapot"])

    assert result.exit_code == 1
    assert "teapot" in ANSI_RE.sub("", result.output)
