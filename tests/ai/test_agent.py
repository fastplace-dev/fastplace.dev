"""Agent — LiteLLM completion routing, tool-call loop, Instructor outputs (T5.2)."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel

import fastplace.ai.agent as agent_module
from fastplace.ai import Agent, Tool, reset_tool_registry
from fastplace.errors import AiError

# ---------------------------------------------------------------- fakes
# Minimal litellm-shaped response objects — the Agent only reads these attrs.


class _Function:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, id: str, name: str, arguments: str):
        self.id = id
        self.type = "function"
        self.function = _Function(name, arguments)


class _Message:
    def __init__(self, content: str | None = None, tool_calls: list[_ToolCall] | None = None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message: _Message, finish_reason: str | None = None):
        self.message = message
        self.finish_reason = finish_reason or (
            "tool_calls" if message.tool_calls else "stop"
        )


class _Response:
    def __init__(self, message: _Message):
        self.choices = [_Choice(message)]


class _ScriptedCompletions:
    """Returns queued responses in order, recording every request."""

    def __init__(self, *responses: _Response):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("completion seam called more times than scripted")
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def _clean():
    reset_tool_registry()
    agent_module._completion_fn = agent_module._default_completion_fn
    agent_module._structured_fn = agent_module._default_structured_fn
    yield
    reset_tool_registry()
    agent_module._completion_fn = agent_module._default_completion_fn
    agent_module._structured_fn = agent_module._default_structured_fn


# ---------------------------------------------------------------- run()


async def test_run_returns_final_text_and_passes_context():
    seam = _ScriptedCompletions(_Response(_Message(content="hello!")))

    agent_module._completion_fn = seam
    agent = Agent(model="gpt-4o", system_prompt="You are Fastplace.")

    assert await agent.run("hi") == "hello!"
    call = seam.calls[0]
    assert call["model"] == "gpt-4o"
    assert call["messages"] == [
        {"role": "system", "content": "You are Fastplace."},
        {"role": "user", "content": "hi"},
    ]
    assert "tools" not in call  # no tools → no tools kwarg


async def test_tool_call_round_trip():
    @Tool(description="echo")
    async def echo(text: str) -> str:
        """Echo.

        Args:
            text: the text.
        """
        return f"echo:{text}"

    seam = _ScriptedCompletions(
        _Response(_Message(tool_calls=[_ToolCall("call_1", "echo", '{"text": "hi"}')])),
        _Response(_Message(content="done")),
    )
    agent_module._completion_fn = seam

    agent = Agent(model="gpt-4o", tools=[echo])
    result = await agent.run("go")

    assert result == "done"
    first, second = seam.calls
    assert first["tools"] == [echo.__fastplace_tool__.to_openai()]
    # conversation carries the assistant tool-call + the tool result
    assert second["messages"] == [
        {"role": "system", "content": ""},
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"text": "hi"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '"echo:hi"'},
    ]


async def test_sync_tool_runs_without_await():
    @Tool(description="sync")
    def add(a: int, b: int) -> int:
        """Add.

        Args:
            a: left.
            b: right.
        """
        return a + b

    seam = _ScriptedCompletions(
        _Response(_Message(tool_calls=[_ToolCall("c1", "add", '{"a": 2, "b": 3}')])),
        _Response(_Message(content="5")),
    )
    agent_module._completion_fn = seam

    await Agent(model="m", tools=[add]).run("go")

    tool_msg = seam.calls[1]["messages"][-1]
    assert tool_msg["content"] == "5"


async def test_tool_exception_becomes_an_error_result_for_the_model(monkeypatch):
    monkeypatch.setenv("APP_DEBUG", "true")

    @Tool(description="boom")
    async def boom() -> str:
        """Boom."""
        raise RuntimeError("disk on fire")

    seam = _ScriptedCompletions(
        _Response(_Message(tool_calls=[_ToolCall("c1", "boom", "{}")])),
        _Response(_Message(content="recovered")),
    )
    agent_module._completion_fn = seam

    result = await Agent(model="m", tools=[boom]).run("go")

    assert result == "recovered"
    tool_msg = seam.calls[1]["messages"][-1]
    payload = json.loads(tool_msg["content"])
    # bounded shape: the class name is the signal; the message only ships
    # when the kernel's debug policy allows detail
    assert payload["error"]["type"] == "RuntimeError"
    assert payload["error"]["message"] == "disk on fire"
    assert tool_msg["tool_call_id"] == "c1"


async def test_tool_exception_message_is_generic_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_DEBUG", "true")  # even a stray debug flag must not leak

    @Tool(description="boom")
    async def boom() -> str:
        """Boom."""
        raise FileNotFoundError("/srv/secrets/db.sock missing")

    seam = _ScriptedCompletions(
        _Response(_Message(tool_calls=[_ToolCall("c1", "boom", "{}")])),
        _Response(_Message(content="recovered")),
    )
    agent_module._completion_fn = seam

    await Agent(model="m", tools=[boom]).run("go")

    tool_msg = seam.calls[1]["messages"][-1]
    payload = json.loads(tool_msg["content"])
    assert payload["error"]["type"] == "FileNotFoundError"
    assert payload["error"]["message"] == "The tool failed."
    assert "/srv/secrets" not in tool_msg["content"]


async def test_invalid_tool_arguments_are_reported_not_raised():
    @Tool(description="e")
    async def echo(text: str) -> str:
        """Echo.

        Args:
            text: t.
        """
        return text

    seam = _ScriptedCompletions(
        _Response(_Message(tool_calls=[_ToolCall("c1", "echo", "not-json{")])),
        _Response(_Message(content="ok")),
    )
    agent_module._completion_fn = seam

    await Agent(model="m", tools=[echo]).run("go")

    tool_msg = seam.calls[1]["messages"][-1]
    assert "invalid arguments" in tool_msg["content"]


@pytest.mark.parametrize("wire", ["null", "[1]", '"x"', "5"])
async def test_non_object_arguments_are_rejected_as_invalid(wire):
    @Tool(description="e")
    async def echo(text: str) -> str:
        """Echo.

        Args:
            text: t.
        """
        return text

    seam = _ScriptedCompletions(
        _Response(_Message(tool_calls=[_ToolCall("c1", "echo", wire)])),
        _Response(_Message(content="ok")),
    )
    agent_module._completion_fn = seam

    await Agent(model="m", tools=[echo]).run("go")

    tool_msg = seam.calls[1]["messages"][-1]
    # valid-but-non-object JSON must land in the invalid-arguments branch,
    # not the TypeError leak path
    assert "expected a JSON object" in tool_msg["content"]


async def test_argument_type_mismatch_is_rejected_before_dispatch():
    calls: list[Any] = []

    @Tool(description="e")
    async def echo(text: str) -> str:
        """Echo.

        Args:
            text: t.
        """
        calls.append(text)
        return text

    seam = _ScriptedCompletions(
        # a dict where the handler declared str — type confusion into the tool
        _Response(_Message(tool_calls=[_ToolCall("c1", "echo", '{"text": {"inject": 1}}')])),
        # an unknown key — must not reach fn(**arguments) as a TypeError
        _Response(_Message(tool_calls=[_ToolCall("c2", "echo", '{"text": "a", "extra": 1}')])),
        _Response(_Message(content="ok")),
    )
    agent_module._completion_fn = seam

    await Agent(model="m", tools=[echo]).run("go")

    assert calls == []  # the handler never executed with corrupt arguments
    for call in seam.calls[1:3]:
        content = call["messages"][-1]["content"]
        assert "invalid arguments" in content
        assert "text" in content


async def test_unknown_tool_name_is_reported_to_the_model():
    seam = _ScriptedCompletions(
        _Response(_Message(tool_calls=[_ToolCall("c1", "ghost", "{}")])),
        _Response(_Message(content="ok")),
    )
    agent_module._completion_fn = seam

    await Agent(model="m", tools=[]).run("go")

    tool_msg = seam.calls[1]["messages"][-1]
    assert "unknown tool" in tool_msg["content"]


async def test_round_limit_is_enforced():
    @Tool(description="loop")
    async def loop_tool() -> str:
        """Loop."""
        return "again"

    seam = _ScriptedCompletions(
        *(
            _Response(_Message(tool_calls=[_ToolCall(f"c{i}", "loop_tool", "{}")]))
            for i in range(10)
        )
    )
    agent_module._completion_fn = seam

    agent = Agent(model="m", tools=[loop_tool], max_tool_rounds=3)
    with pytest.raises(AiError, match="3 tool rounds"):
        await agent.run("go")


async def test_max_tool_rounds_zero_is_honored():
    # 0 is an explicit "never execute tools" bound, not a falsy accident
    agent = Agent(model="m", max_tool_rounds=0)
    assert agent.max_tool_rounds == 0

    seam = _ScriptedCompletions(
        _Response(_Message(tool_calls=[_ToolCall("c1", "ghost", "{}")])),
    )
    agent_module._completion_fn = seam

    with pytest.raises(AiError, match="0 tool rounds"):
        await agent.run("go")


# ------------------------------------------------- tool result safety


class _Row(BaseModel):
    sku: str
    qty: int


async def test_tool_returning_a_pydantic_model_completes_the_loop():
    @Tool(description="fetch")
    async def fetch_row(sku: str) -> _Row:
        """Fetch a row.

        Args:
            sku: what to fetch.
        """
        return _Row(sku=sku, qty=2)

    seam = _ScriptedCompletions(
        _Response(_Message(tool_calls=[_ToolCall("c1", "fetch_row", '{"sku": "x"}')])),
        _Response(_Message(content="done")),
    )
    agent_module._completion_fn = seam

    result = await Agent(model="m", tools=[fetch_row]).run("go")

    assert result == "done"
    tool_msg = seam.calls[1]["messages"][-1]
    assert json.loads(tool_msg["content"]) == {"sku": "x", "qty": 2}


async def test_tool_returning_datetime_serializes_as_isoformat():
    stamp = datetime(2026, 9, 14, 12, 30, tzinfo=UTC)

    @Tool(description="now")
    async def now() -> datetime:
        """Now."""
        return stamp

    seam = _ScriptedCompletions(
        _Response(_Message(tool_calls=[_ToolCall("c1", "now", "{}")])),
        _Response(_Message(content="done")),
    )
    agent_module._completion_fn = seam

    await Agent(model="m", tools=[now]).run("go")

    tool_msg = seam.calls[1]["messages"][-1]
    assert json.loads(tool_msg["content"]) == stamp.isoformat()


async def test_unserializable_tool_result_degrades_to_an_error_payload():
    @Tool(description="cyc")
    async def cyc() -> list:  # type: ignore[type-arg]
        """Return a self-referencing list."""
        data: list[Any] = []
        data.append(data)  # json.dumps raises ValueError (circular)
        return data

    seam = _ScriptedCompletions(
        _Response(_Message(tool_calls=[_ToolCall("c1", "cyc", "{}")])),
        _Response(_Message(content="recovered")),
    )
    agent_module._completion_fn = seam

    result = await Agent(model="m", tools=[cyc]).run("go")

    assert result == "recovered"  # the loop survives; the model sees the failure
    tool_msg = seam.calls[1]["messages"][-1]
    payload = json.loads(tool_msg["content"])
    assert "not serializable" in payload["error"]


async def test_history_is_threaded_into_the_conversation():
    seam = _ScriptedCompletions(_Response(_Message(content="ok")))
    agent_module._completion_fn = seam

    history = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "sure"},
    ]
    await Agent(model="m").run("now", history=history)

    messages = seam.calls[0]["messages"]
    assert messages == [
        {"role": "system", "content": ""},
        *history,
        {"role": "user", "content": "now"},
    ]


async def test_model_defaults_from_config(monkeypatch):
    monkeypatch.setenv("AI_MODEL", "anthropic/claude-sonnet")
    seam = _ScriptedCompletions(_Response(_Message(content="ok")))
    agent_module._completion_fn = seam

    await Agent().run("hi")

    assert seam.calls[0]["model"] == "anthropic/claude-sonnet"


def test_tools_must_be_tool_decorated():
    async def plain(query: str) -> str:
        return query

    with pytest.raises(ValueError, match="plain"):
        Agent(model="m", tools=[plain])


# ------------------------------------------------- structured outputs


class _Answer(BaseModel):
    answer: str


async def test_response_model_returns_a_structured_instance():
    captured: dict[str, Any] = {}

    agent_module._completion_fn = _ScriptedCompletions(_Response(_Message(content="raw")))

    async def fake_structured(**kwargs: Any) -> _Answer:
        captured.update(kwargs)
        return _Answer(answer="42")

    agent_module._structured_fn = fake_structured

    agent = Agent(model="m", response_model=_Answer)
    result = await agent.run("meaning of life?")

    assert isinstance(result, _Answer)
    assert result.answer == "42"
    assert captured["response_model"] is _Answer
    assert captured["model"] == "m"
    # the structured call sees the model's own draft turn — Instructor
    # reformats it under the schema
    assert captured["messages"][-2] == {"role": "user", "content": "meaning of life?"}
    assert captured["messages"][-1] == {"role": "assistant", "content": "raw"}


async def test_structured_output_runs_the_tool_loop_first():
    @Tool(description="lookup")
    async def lookup(key: str) -> str:
        """Lookup.

        Args:
            key: k.
        """
        return f"val:{key}"

    completion = _ScriptedCompletions(
        _Response(_Message(tool_calls=[_ToolCall("c1", "lookup", '{"key": "a"}')])),
        # a tool-free turn ends the loop and hands off to the structured call
        _Response(_Message(content="looked up")),
    )
    agent_module._completion_fn = completion

    async def fake_structured(**kwargs: Any) -> _Answer:
        captured.update(kwargs)
        return _Answer(answer="done")

    captured: dict[str, Any] = {}
    agent_module._structured_fn = fake_structured

    agent = Agent(model="m", tools=[lookup], response_model=_Answer)
    await agent.run("go")

    # the structured call sees the tool exchange in its message list

    # the structured call sees the tool exchange in its message list
    roles = [m["role"] for m in captured["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
