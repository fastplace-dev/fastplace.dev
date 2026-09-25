"""SSE streaming for agents — ``agent.stream_response(...)``.

Event protocol (kept byte-compatible with ``@fastplace/ai-react``):

* ``delta`` — ``{"token": "…"}`` one LLM token chunk
* ``tool``  — ``{"name", "arguments", "result"}`` a tool executed server-side
* ``done``  — ``{"content": full text[, "data": structured output]}``
* ``error`` — ``{"message": "…"}`` terminal failure (the stream still ends
  cleanly — HTTP headers are already out by then)

Errors after the stream starts cannot change the status code, so they are
reported as an ``error`` event instead of raising through ASGI.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

from starlette.responses import StreamingResponse

from fastplace.ai import agent as agent_module
from fastplace.ai.agent import debug_details_enabled, dump_tool_result, tool_result_payload

logger = logging.getLogger("fastplace.ai")


def sse_event(event: str, data: dict[str, Any]) -> str:
    """Format one SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def parse_sse(wire: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse concatenated SSE frames — tests and frontend-parity checks."""
    events: list[tuple[str, dict[str, Any]]] = []
    for block in wire.split("\n\n"):
        name = "message"
        data: dict[str, Any] = {}
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data = json.loads(line.removeprefix("data:").strip())
        if block.strip():
            events.append((name, data))
    return events


def stream_response(
    agent: Any,
    message: str | None,
    history: list[dict[str, Any]] | None = None,
) -> StreamingResponse:
    """Wrap the agent loop in an SSE response for ``routes/ai.py`` endpoints.

    An invalid message raises before the response is built, so the kernel's
    exception handlers can still answer with a proper 422.
    """
    messages = agent._conversation(message, history)  # validates eagerly
    return StreamingResponse(
        _events(agent, messages),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def stream_events(
    agent: Any,
    message: str | None,
    history: list[dict[str, Any]] | None = None,
) -> Any:
    """Public async iterator of raw SSE frames — terminal replay of the stream.

    ``ai:chat --stream`` consumes this (parsing each frame with
    :func:`parse_sse`) so the CLI never reaches for the private ``_events``
    generator. The message is validated eagerly, exactly like
    ``stream_response``.
    """
    messages = agent._conversation(message, history)  # validates eagerly
    return _events(agent, messages)


async def _events(agent: Any, messages: list[dict[str, Any]]) -> Any:
    rounds = 0
    try:
        while True:
            stream = await agent_module._completion_fn(**agent._request(messages, stream=True))
            # Tool calls fragment across stream chunks — accumulate by the
            # provider's fragment index and assemble when the round ends.
            fragments: dict[int, dict[str, Any]] = {}
            # Scoped to this round: interstitial text a model emits next to a
            # tool call belongs to that round's transcript turn only.
            round_text: list[str] = []
            # aclosing: a client disconnect must close the provider stream
            # deterministically, not whenever the GC gets around to it.
            async with contextlib.aclosing(stream):
                async for chunk in stream:
                    choice = chunk.choices[0]
                    delta = choice.delta
                    content = getattr(delta, "content", None)
                    if content:
                        round_text.append(content)
                        yield sse_event("delta", {"token": content})
                    for call in getattr(delta, "tool_calls", None) or []:
                        fragment = fragments.setdefault(
                            call.index, {"id": None, "name": None, "arguments": ""}
                        )
                        if getattr(call, "id", None):
                            fragment["id"] = call.id
                        function = getattr(call, "function", None)
                        if function is not None:
                            if function.name:
                                fragment["name"] = function.name
                            if function.arguments:
                                fragment["arguments"] += function.arguments
            if not fragments:
                # Parity with Agent.run: the transcript (and the structured
                # call) sees the final assistant turn, and `done` carries
                # exactly the answer run() would return.
                final_text = "".join(round_text)
                messages.append({"role": "assistant", "content": final_text})
                done: dict[str, Any] = {"content": final_text}
                if agent.response_model is not None:
                    structured = await agent._structured(messages)
                    done["data"] = structured.model_dump()
                yield sse_event("done", done)
                return
            rounds += 1
            if rounds > agent.max_tool_rounds:
                yield sse_event(
                    "error",
                    {
                        "message": (
                            f"agent exceeded {agent.max_tool_rounds} tool rounds "
                            "without a final answer"
                        )
                    },
                )
                return
            messages.append(_assistant_fragments_message(fragments, "".join(round_text)))
            for index in sorted(fragments):
                fragment = fragments[index]
                result = await agent._execute(_FragmentCall(fragment))
                try:
                    arguments: Any = json.loads(fragment["arguments"] or "{}")
                except ValueError:
                    arguments = fragment["arguments"]
                yield sse_event(
                    "tool",
                    {
                        "name": fragment["name"],
                        "arguments": arguments,
                        "result": tool_result_payload(result),
                    },
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": fragment["id"],
                        "content": dump_tool_result(result),
                    }
                )
    except Exception as exc:  # noqa: BLE001 — headers are out; report, don't crash
        logger.exception("agent stream failed")
        yield sse_event(
            "error",
            {"message": str(exc) if debug_details_enabled() else "The AI stream failed."},
        )


class _FragmentCall:
    """Adapter letting ``Agent._execute`` consume an assembled fragment."""

    def __init__(self, fragment: dict[str, Any]):
        self.id = fragment["id"]

        class _Function:
            def __init__(self, name: Any, arguments: Any) -> None:
                self.name = name
                self.arguments = arguments

        self.function = _Function(fragment["name"], fragment["arguments"])


def _assistant_fragments_message(
    fragments: dict[int, dict[str, Any]], content: str = ""
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": fragment["id"],
                "type": "function",
                "function": {
                    "name": fragment["name"],
                    "arguments": fragment["arguments"],
                },
            }
            for _, fragment in sorted(fragments.items())
        ],
    }
