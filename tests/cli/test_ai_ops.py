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
    result = runner.invoke(cli_app, ["ai:tool:run", "echo", "--args", '{"text": "hi", "times": 2}'])

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


AGENTS_MODULE = """from fastplace.ai import Agent


def helper_agent() -> Agent:
    return Agent(model="factory-model-x")
"""


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


# Review fix 3 — a broken project import (name collisions, module-cache
# churn, broken project code in a reused process) must degrade to the
# in-process registry view, never crash a read-only listing command.
def _boom_import(root):
    raise RuntimeError("project import broken")


def test_tool_run_degrades_when_project_import_fails(
    tools_project,
    park_project_modules,  # noqa: F811 — fixture param; pytest resolves it by name
    monkeypatch,
):
    import fastplace.ai as ai_package

    monkeypatch.setattr(ai_package, "import_tools", _boom_import)

    result = runner.invoke(cli_app, ["ai:tool:run", "echo", "--args", '{"text": "hi"}'])

    assert result.exit_code == 1
    plain = ANSI_RE.sub("", result.output)
    assert "project import unavailable" in plain
    assert "no tool named 'echo'" in plain


# Review fix 4 — a provider failure mid-stream must print one clean red
# line and exit 1, matching the non-stream path.
def test_chat_stream_provider_failure_exits_one_cleanly(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)

    import fastplace.ai.stream as stream_module

    async def _boom_stream(agent, message):
        raise RuntimeError("stream provider down")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(stream_module, "stream_events", _boom_stream)

    result = runner.invoke(cli_app, ["ai:chat", "hi", "--stream"])

    assert result.exit_code == 1
    plain = ANSI_RE.sub("", result.output)
    assert "stream provider down" in plain
    assert "Traceback" not in plain


def test_tool_show_degrades_when_project_import_fails(
    tools_project,
    park_project_modules,  # noqa: F811 — fixture param; pytest resolves it by name
    monkeypatch,
):
    import fastplace.ai as ai_package

    monkeypatch.setattr(ai_package, "import_tools", _boom_import)

    result = runner.invoke(cli_app, ["ai:tool:show", "echo"])

    assert result.exit_code == 1
    plain = ANSI_RE.sub("", result.output)
    assert "project import unavailable" in plain
    assert "no tool named 'echo'" in plain


def test_agents_degrades_when_project_import_fails(
    agents_project,
    park_project_modules,  # noqa: F811 — fixture param; pytest resolves it by name
    monkeypatch,
):
    import fastplace.ai as ai_package

    monkeypatch.setattr(ai_package, "import_agents", _boom_import)

    result = runner.invoke(cli_app, ["ai:agents"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "project import unavailable" in plain
    assert "no agent factories" in plain


def test_chat_agent_discovery_degrades_when_project_import_fails(
    agents_project,
    park_project_modules,  # noqa: F811 — fixture param; pytest resolves it by name
    monkeypatch,
):
    import fastplace.ai as ai_package

    monkeypatch.setattr(ai_package, "import_agents", _boom_import)

    result = runner.invoke(cli_app, ["ai:chat", "hi", "--agent", "helper_agent"])

    assert result.exit_code == 1
    plain = ANSI_RE.sub("", result.output)
    assert "project import unavailable" in plain
    assert "no agent named 'helper_agent'" in plain


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


# ---------------------------------------------------------------------------
# ai:agents
# ---------------------------------------------------------------------------


def test_agents_lists_factory_model_and_tool_count(agents_project, park_project_modules):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    result = runner.invoke(cli_app, ["ai:agents"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "helper_agent" in plain
    assert "app.ai.agents.helper" in plain
    assert "factory-model-x" in plain
    assert "0" in plain  # tool count for a bare agent


def test_agents_empty_is_dim_exit_zero(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    (tmp_path / "asgi.py").write_text("# marker — a project with no agents package\n")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["ai:agents"])

    assert result.exit_code == 0, result.output
    assert "no agent factories" in ANSI_RE.sub("", result.output)


def test_agents_broken_factory_is_a_row_not_a_crash(tmp_path, monkeypatch, park_project_modules):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    broken = "from fastplace.ai import Agent\n\n\ndef broken_agent() -> Agent:\n    raise RuntimeError('boom')\n"
    (tmp_path / "asgi.py").write_text("# marker\n")
    for package in ("app", "app/ai", "app/ai/agents"):
        (tmp_path / package).mkdir(parents=True, exist_ok=True)
        (tmp_path / package / "__init__.py").write_text("")
    (tmp_path / "app/ai/agents/broken.py").write_text(broken)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["ai:agents"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "broken_agent" in plain and "boom" in plain  # the failure is row-level


# ---------------------------------------------------------------------------
# ai:embed — stubbed _embedding_fn, --check against declared VectorField dims
# ---------------------------------------------------------------------------


@pytest.fixture()
def embed_stub(monkeypatch):
    """Stub the provider seam: 1536-float vectors, calls recorded."""
    import fastplace.ai.embeddings as embeddings_module

    calls: list[dict[str, Any]] = []

    async def _fake_embedding_fn(*, model=None, input=None):  # noqa: A002
        calls.append({"model": model, "input": list(input or [])})
        return [[0.001] * 1536 for _ in (input or [])]

    monkeypatch.setattr(embeddings_module, "_embedding_fn", _fake_embedding_fn)
    return calls


VECTOR_MODEL_TEMPLATE = """from fastplace.orm import Model
from fastplace.orm import VectorField


class Document(Model):
    __tablename__ = "embed_documents_{suffix}"

    body: "list[float] | None" = VectorField(dimensions={dims})
"""

# NOTE before writing the fixture: confirm the repo's canonical import path for
# Model/VectorField with `git grep -n 'VectorField' tests/orm | head -5` and
# match it — the template below assumes `from fastplace.orm import ...`; only
# the fixture project's SOURCE is adjustable, the assertions are contractual.

PG_URL = "postgresql+asyncpg://embed:embed@localhost:5432/embed"  # types build at import; no live DB contacted


def _vector_project(tmp_path, monkeypatch, dims: int, suffix: str, url: str):
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("APP_ENV", raising=False)
    (tmp_path / "asgi.py").write_text("# marker — the _project_root() check\n")
    for package in ("app", "app/models"):
        (tmp_path / package).mkdir(parents=True, exist_ok=True)
        (tmp_path / package / "__init__.py").write_text("")
    (tmp_path / "app/models/document.py").write_text(
        VECTOR_MODEL_TEMPLATE.format(dims=dims, suffix=suffix)
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_embed_prints_model_dimension_and_first_floats(tmp_path, monkeypatch, embed_stub):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["ai:embed", "hello world", "--model", "embed-model-x"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "embed-model-x" in plain
    assert "1536" in plain  # dimension reported
    assert "0.001" in plain  # first floats render


def test_embed_check_match_exits_zero(tmp_path, monkeypatch, park_project_modules, embed_stub):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    _vector_project(tmp_path, monkeypatch, dims=1536, suffix="match", url=PG_URL)

    result = runner.invoke(cli_app, ["ai:embed", "text", "--check", "Document"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "match" in plain and "Document" in plain


def test_embed_check_mismatch_exits_one(tmp_path, monkeypatch, park_project_modules, embed_stub):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    _vector_project(tmp_path, monkeypatch, dims=512, suffix="mismatch", url=PG_URL)

    result = runner.invoke(cli_app, ["ai:embed", "text", "--check", "Document"])

    assert result.exit_code == 1
    plain = ANSI_RE.sub("", result.output)
    assert "mismatch" in plain
    assert "512" in plain and "1536" in plain  # both sides of the verdict shown


def test_embed_check_unknown_class_exits_one(
    tmp_path,
    monkeypatch,
    park_project_modules,  # noqa: F811 — fixture param
    embed_stub,  # noqa: F811 — fixture param
):
    _vector_project(tmp_path, monkeypatch, dims=512, suffix="unknown", url=PG_URL)

    result = runner.invoke(cli_app, ["ai:embed", "text", "--check", "Teapot"])

    assert result.exit_code == 1
    assert "Teapot" in ANSI_RE.sub("", result.output)


def test_embed_check_sqlite_backend_notes_missing_dims(
    tmp_path,
    monkeypatch,
    park_project_modules,  # noqa: F811 — fixture param
    embed_stub,
):
    url = f"sqlite+aiosqlite:///{tmp_path}/embed.db"
    _vector_project(tmp_path, monkeypatch, dims=512, suffix="sqlite", url=url)

    result = runner.invoke(cli_app, ["ai:embed", "text", "--check", "Document"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "not declared" in plain  # VectorJSON carries no dims — stated, not guessed
