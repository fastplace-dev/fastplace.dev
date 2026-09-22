"""Agent.stream_response — SSE token/tool/done events over ASGI (T5.3)."""

from typing import Any

import pytest
from pydantic import BaseModel

import fastplace.ai.agent as agent_module
from fastplace.ai import Agent, Tool, reset_tool_registry
from fastplace.ai.stream import parse_sse, sse_event
from fastplace.errors import ValidationError

# ---------------------------------------------------------------- fakes


class _Fn:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _DeltaToolCall:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index = index
        self.id = id
        self.type = "function"
        self.function = _Fn(name=name, arguments=arguments)


class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _StreamChoice:
    def __init__(self, delta=None, finish_reason=None):
        self.delta = delta or _Delta()
        self.finish_reason = finish_reason


class _Chunk:
    def __init__(self, choice=None):
        self.choices = [choice or _StreamChoice()]


class _ScriptedStreams:
    """Queues whole stream rounds; each round is a list of chunks."""

    def __init__(self, *rounds: list[_Chunk]):
        self.rounds = list(rounds)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any):
        self.calls.append(kwargs)
        if not self.rounds:
            raise AssertionError("stream seam called more times than scripted")
        round_chunks = self.rounds.pop(0)

        async def iterator():
            for chunk in round_chunks:
                yield chunk

        return iterator()


def _text_round(*tokens: str) -> list[_Chunk]:
    return [
        *(_Chunk(_StreamChoice(_Delta(content=t))) for t in tokens),
        _Chunk(_StreamChoice(finish_reason="stop")),
    ]


@pytest.fixture(autouse=True)
def _clean():
    reset_tool_registry()
    agent_module._completion_fn = agent_module._default_completion_fn
    agent_module._structured_fn = agent_module._default_structured_fn
    yield
    reset_tool_registry()
    agent_module._completion_fn = agent_module._default_completion_fn
    agent_module._structured_fn = agent_module._default_structured_fn


async def _drain(agent: Agent, message: str) -> list[tuple[str, dict]]:
    """Collect (event, data) pairs from the streaming response."""
    response = agent.stream_response(message)
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    body = b""
    async for chunk in response.body_iterator:
        body += chunk if isinstance(chunk, bytes) else chunk.encode()
    return parse_sse(body.decode())


# ---------------------------------------------------------------- unit


def test_sse_event_wire_format():
    assert sse_event("delta", {"token": "hi"}) == 'event: delta\ndata: {"token": "hi"}\n\n'


def test_parse_sse_handles_multiple_events():
    wire = sse_event("delta", {"token": "a"}) + sse_event("done", {"content": "ab"})
    assert parse_sse(wire) == [("delta", {"token": "a"}), ("done", {"content": "ab"})]


# ---------------------------------------------------------------- stream


async def test_tokens_stream_then_done():
    seam = _ScriptedStreams(_text_round("Hel", "lo!"))
    agent_module._completion_fn = seam

    events = await _drain(Agent(model="m"), "hi")

    assert events == [
        ("delta", {"token": "Hel"}),
        ("delta", {"token": "lo!"}),
        ("done", {"content": "Hello!"}),
    ]


async def test_tool_fragments_assemble_and_the_loop_continues():
    @Tool(description="echo")
    async def echo(text: str) -> str:
        """Echo.

        Args:
            text: the text.
        """
        return f"echo:{text}"

    # Providers fragment one tool call across chunks: name+id first,
    # then the JSON arguments in pieces.
    tool_round = [
        _Chunk(_StreamChoice(_Delta(tool_calls=[_DeltaToolCall(0, id="c1", name="echo")]))),
        _Chunk(_StreamChoice(_Delta(tool_calls=[_DeltaToolCall(0, arguments='{"text":')]))),
        _Chunk(_StreamChoice(_Delta(tool_calls=[_DeltaToolCall(0, arguments=' "hi"}')]))),
        _Chunk(_StreamChoice(finish_reason="tool_calls")),
    ]
    seam = _ScriptedStreams(tool_round, _text_round("done text"))
    agent_module._completion_fn = seam

    events = await _drain(Agent(model="m", tools=[echo]), "go")

    assert ("tool", {"name": "echo", "arguments": {"text": "hi"}, "result": "echo:hi"}) in events
    assert events[-1] == ("done", {"content": "done text"})


class _Answer(BaseModel):
    answer: str


async def test_structured_stream_appends_data_to_done():
    seam = _ScriptedStreams(_text_round("draft"))
    agent_module._completion_fn = seam
    captured: dict[str, Any] = {}

    async def fake_structured(**kwargs: Any) -> _Answer:
        captured.update(kwargs)
        return _Answer(answer="42")

    agent_module._structured_fn = fake_structured

    events = await _drain(Agent(model="m", response_model=_Answer), "q")

    assert events[-1] == ("done", {"content": "draft", "data": {"answer": "42"}})
    # parity with Agent.run: the structured call sees the drafted answer as
    # the closing assistant turn, not a transcript ending on a tool result
    assert captured["messages"][-1] == {"role": "assistant", "content": "draft"}


async def test_provider_failure_emits_an_error_event_not_an_exception(monkeypatch):
    monkeypatch.setenv("APP_DEBUG", "true")

    async def boom(**kwargs: Any):
        raise RuntimeError("provider down")

    agent_module._completion_fn = boom

    events = await _drain(Agent(model="m"), "hi")

    assert events == [("error", {"message": "provider down"})]


async def test_provider_failure_message_is_generic_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_DEBUG", "true")  # stray debug flag must not leak

    async def boom(**kwargs: Any):
        raise RuntimeError("api_base=http://internal-host:9000 key=sk-live-123")

    agent_module._completion_fn = boom

    events = await _drain(Agent(model="m"), "hi")

    # mirrors the kernel's error policy: no raw provider text from production
    assert events == [("error", {"message": "The AI stream failed."})]


async def test_tool_round_interstitial_text_is_scoped_to_its_round():
    @Tool(description="echo")
    async def echo(text: str) -> str:
        """Echo.

        Args:
            text: t.
        """
        return f"echo:{text}"

    tool_round = [
        _Chunk(_StreamChoice(_Delta(content="Let me check."))),
        _Chunk(_StreamChoice(_Delta(tool_calls=[_DeltaToolCall(0, id="c1", name="echo")]))),
        _Chunk(_StreamChoice(_Delta(tool_calls=[_DeltaToolCall(0, arguments='{"text":')]))),
        _Chunk(_StreamChoice(_Delta(tool_calls=[_DeltaToolCall(0, arguments=' "hi"}')]))),
        _Chunk(_StreamChoice(finish_reason="tool_calls")),
    ]
    seam = _ScriptedStreams(tool_round, _text_round("done text"))
    agent_module._completion_fn = seam

    events = await _drain(Agent(model="m", tools=[echo]), "go")

    # done carries only the final round's text — the same answer run() gives
    assert events[-1] == ("done", {"content": "done text"})
    # the transcript tells the provider what it actually said in the tool round
    assistant_turn = seam.calls[1]["messages"][2]
    assert assistant_turn["role"] == "assistant"
    assert assistant_turn["content"] == "Let me check."
    assert assistant_turn["tool_calls"][0]["function"]["name"] == "echo"


async def test_unserializable_tool_result_does_not_kill_the_stream():
    @Tool(description="cyc")
    async def cyc() -> list:  # type: ignore[type-arg]
        """Return a self-referencing list."""
        data: list[Any] = []
        data.append(data)
        return data

    tool_round = [
        _Chunk(_StreamChoice(_Delta(tool_calls=[_DeltaToolCall(0, id="c1", name="cyc")]))),
        _Chunk(_StreamChoice(finish_reason="tool_calls")),
    ]
    seam = _ScriptedStreams(tool_round, _text_round("recovered"))
    agent_module._completion_fn = seam

    events = await _drain(Agent(model="m", tools=[cyc]), "go")

    tool_event = next(event for event in events if event[0] == "tool")
    assert "not serializable" in tool_event[1]["result"]["error"]
    assert events[-1] == ("done", {"content": "recovered"})


async def test_client_disconnect_closes_the_provider_stream():
    closed = {"value": False}

    async def seam(**kwargs: Any):
        async def endless():
            try:
                yield _Chunk(_StreamChoice(_Delta(content="x")))
                yield _Chunk(_StreamChoice(_Delta(content="y")))
                yield _Chunk(_StreamChoice(_Delta(content="z")))
            finally:
                closed["value"] = True

        return endless()

    agent_module._completion_fn = seam

    response = Agent(model="m").stream_response("hi")
    generator = response.body_iterator
    await anext(generator)  # first delta is out…
    await generator.aclose()  # …then the client disconnects mid-stream

    # the provider iterator must be closed deterministically, not at GC
    assert closed["value"] is True


async def test_round_limit_emits_an_error_event():
    @Tool(description="loop")
    async def loop_tool() -> str:
        """Loop."""
        return "again"

    def tool_round():
        return [
            _Chunk(
                _StreamChoice(_Delta(tool_calls=[_DeltaToolCall(0, id="c1", name="loop_tool")]))
            ),
            _Chunk(_StreamChoice(finish_reason="tool_calls")),
        ]

    seam = _ScriptedStreams(*[tool_round() for _ in range(5)])
    agent_module._completion_fn = seam

    events = await _drain(Agent(model="m", tools=[loop_tool], max_tool_rounds=2), "go")

    assert events[-1][0] == "error"
    assert "tool rounds" in events[-1][1]["message"]


def test_empty_message_is_rejected_before_the_stream_starts():
    with pytest.raises(ValidationError):
        Agent(model="m").stream_response("   ")


# ------------------------------------------------- kernel integration


def _ai_app():
    import httpx

    from fastplace.auth.middleware import CsrfMiddleware, ResolveUserMiddleware
    from fastplace.http.kernel import get_app
    from fastplace.http.router import Router

    class AssistantController:
        async def store(self, request):
            payload = await request.json()
            agent = Agent(model="m", system_prompt="You are Fastplace.")
            return agent.stream_response(payload.get("message"))

    router = Router()
    router.post("/assistant", AssistantController, "store", name="ai.assistant")

    app = get_app(
        ai_routes=router,
        middleware=[ResolveUserMiddleware(), CsrfMiddleware()],
        config={"APP_ENV": "local", "APP_KEY": "test-app-key-not-for-production-use-only"},
    )
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_ai_route_streams_sse_through_the_kernel():
    agent_module._completion_fn = _ScriptedStreams(_text_round("hi ", "there"))

    async with _ai_app() as client:
        # any safe GET first — it issues the session cookie + CSRF token header
        page = await client.get("/api/openapi.json")
        token = page.headers["X-Fastplace-CSRF-Token"]

        response = await client.post(
            "/ai/assistant",
            json={"message": "hello"},
            headers={"X-Fastplace-CSRF-Token": token},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert events[0] == ("delta", {"token": "hi "})
    assert events[-1] == ("done", {"content": "hi there"})


async def test_ai_routes_are_csrf_protected():
    agent_module._completion_fn = _ScriptedStreams(_text_round("x"))

    async with _ai_app() as client:
        await client.get("/api/openapi.json")  # session + token issued
        # unsafe POST without the CSRF token — even though the session is live
        response = await client.post("/ai/assistant", json={"message": "hello"})

    assert response.status_code == 419
