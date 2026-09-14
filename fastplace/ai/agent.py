"""Agent — provider-agnostic LLM agent with a tool-call loop.

``Agent(model, system_prompt, tools)`` routes completions through LiteLLM
(any provider), executes ``@Tool`` handlers the model calls, and validates
structured outputs through Instructor when ``response_model`` is given
(blueprint §9). Providers are reached only behind the module seam functions
so tests (and local fakes) never touch the network.
"""

from __future__ import annotations

import inspect
import json
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel
from pydantic import ValidationError as ArgumentsValidationError

from fastplace.ai.tool import ToolFn, ToolSpec, args_model
from fastplace.config import config
from fastplace.errors import AiError

logger = logging.getLogger("fastplace.ai")


@runtime_checkable
class CompletionFn(Protocol):
    """The seam every completion goes through (tests replace this)."""

    async def __call__(self, **kwargs: Any) -> Any: ...


async def _default_completion_fn(**kwargs: Any) -> Any:
    import litellm

    return await litellm.acompletion(**kwargs)


async def _default_structured_fn(**kwargs: Any) -> Any:
    import instructor
    import litellm

    client = instructor.from_litellm(litellm.acompletion)
    return await client.chat.completions.create(**kwargs)


#: Patch seams — module attributes so tests can swap providers out.
_completion_fn: CompletionFn = _default_completion_fn
_structured_fn: CompletionFn = _default_structured_fn


def debug_details_enabled() -> bool:
    """The kernel's error policy, mirrored here: exception detail only when
    APP_DEBUG is on *and* the environment is not production."""
    return bool(config("APP_DEBUG", default=False)) and config(
        "APP_ENV", default="local"
    ) != "production"


def _jsonable(value: Any) -> Any:
    """Coerce one otherwise-unserializable value into a JSON-safe shape."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID, timedelta)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=repr)
    return str(value)


def dump_tool_result(result: Any) -> str:
    """Serialize a tool result for the model transcript; never raises.

    A handler returning something the JSON encoder cannot express (a
    circular structure, an exotic object) degrades to a bounded error
    payload instead of killing the tool loop mid-answer.
    """
    try:
        return json.dumps(result, default=_jsonable)
    except (TypeError, ValueError):
        return json.dumps({"error": "tool result not serializable"})


def tool_result_payload(result: Any) -> Any:
    """The JSON-safe form of a tool result for SSE ``tool`` events."""
    return json.loads(dump_tool_result(result))


def resolve_tool(tool: ToolFn | ToolSpec) -> ToolSpec:
    """Find the ToolSpec behind a function (or pass a spec through)."""
    if isinstance(tool, ToolSpec):
        return tool
    spec = getattr(tool, "__fastplace_tool__", None)
    if spec is None:
        raise ValueError(
            f"tool '{getattr(tool, '__name__', tool)}' is not registered with @Tool — "
            "decorate it before passing it to Agent"
        )
    return spec


class Agent:
    """A conversational agent assembled per endpoint in ``routes/ai.py``.

    ``run`` executes the full tool loop and returns the final text (or a
    validated ``response_model`` instance); ``stream_response`` wraps the
    same loop in an SSE streaming response for the frontend hooks.
    """

    def __init__(
        self,
        model: str | None = None,
        system_prompt: str = "",
        tools: list[ToolFn | ToolSpec] | None = None,
        response_model: type[BaseModel] | None = None,
        max_tool_rounds: int | None = None,
        temperature: float | None = None,
    ) -> None:
        self.model = model or config("AI_MODEL", default="gpt-4o-mini")
        self.system_prompt = system_prompt
        self.tools = [resolve_tool(t) for t in (tools or [])]
        self.response_model = response_model
        # 0 is a real bound ("never execute tools"), not a falsy accident.
        self.max_tool_rounds = (
            max_tool_rounds
            if max_tool_rounds is not None
            else int(config("AI_MAX_TOOL_ROUNDS", default=8))
        )
        self.temperature = temperature

    # ---------------------------------------------------------------- run

    async def run(
        self, message: str, history: list[dict[str, Any]] | None = None
    ) -> str | BaseModel:
        """Run the tool loop to completion and return the final answer."""
        messages = self._conversation(message, history)
        rounds = 0
        while True:
            response = await _completion_fn(**self._request(messages, stream=False))
            choice = response.choices[0]
            tool_calls = list(getattr(choice.message, "tool_calls", None) or [])
            if not tool_calls:
                messages.append(
                    {"role": "assistant", "content": choice.message.content or ""}
                )
                if self.response_model is None:
                    return choice.message.content or ""
                return await self._structured(messages)
            rounds += 1
            if rounds > self.max_tool_rounds:
                raise AiError(
                    f"agent exceeded {self.max_tool_rounds} tool rounds without a final answer"
                )
            messages.append(_assistant_tool_message(choice.message, tool_calls))
            for tool_call in tool_calls:
                result = await self._execute(tool_call)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": dump_tool_result(result),
                    }
                )

    # ---------------------------------------------------------- streaming

    def stream_response(
        self, message: str | None, history: list[dict[str, Any]] | None = None
    ) -> Any:
        """An SSE StreamingResponse of the same loop (see fastplace.ai.stream)."""
        from fastplace.ai.stream import stream_response

        return stream_response(self, message, history=history)

    # ---------------------------------------------------------- internals

    def _conversation(
        self, message: str | None, history: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        if not message or not message.strip():
            from fastplace.errors import ValidationError

            raise ValidationError("message is required")
        conversation: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt}
        ]
        conversation.extend(history or [])
        conversation.append({"role": "user", "content": message})
        return conversation

    def _request(self, messages: list[dict[str, Any]], *, stream: bool) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.model,
            # Snapshot: the loop keeps appending to `messages` after the
            # request is built; callers (and test seams) must see exactly
            # what was sent.
            "messages": list(messages),
            "stream": stream,
        }
        if self.tools:
            request["tools"] = [spec.to_openai() for spec in self.tools]
        if self.temperature is not None:
            request["temperature"] = self.temperature
        return request

    async def _structured(self, messages: list[dict[str, Any]]) -> BaseModel:
        assert self.response_model is not None
        # The final structured call drops tools — Instructor needs a clean
        # completion constrained to the response schema.
        return await _structured_fn(
            response_model=self.response_model,
            model=self.model,
            messages=messages,
        )

    async def _execute(self, tool_call: Any) -> Any:
        """Run one requested tool; failures become error payloads the model sees."""
        name = tool_call.function.name
        spec = next((t for t in self.tools if t.name == name), None)
        if spec is None:
            return {"error": f"unknown tool '{name}'"}
        try:
            arguments = json.loads(tool_call.function.arguments or "{}")
        except (TypeError, ValueError) as exc:
            return {"error": f"invalid arguments for '{name}': {exc}"}
        if not isinstance(arguments, dict):
            return {"error": f"invalid arguments for '{name}': expected a JSON object"}
        # Model-controlled JSON is validated against the handler's signature
        # before dispatch — unknown keys are rejected and declared types
        # checked, so nothing unvetted reaches Python argument binding.
        try:
            validated = args_model(spec).model_validate(arguments)
        except ArgumentsValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: {error['msg']}"
                for error in exc.errors(include_url=False)
            )
            seen = ", ".join(sorted(arguments)) or "(none)"
            return {"error": f"invalid arguments for '{name}': {details} (received: {seen})"}
        try:
            result = spec.fn(**{key: getattr(validated, key) for key in arguments})
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as exc:  # noqa: BLE001 — the model must see tool failures
            logger.exception("tool '%s' raised during dispatch", name)
            return {
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc) if debug_details_enabled() else "The tool failed.",
                }
            }


def _assistant_tool_message(message: Any, tool_calls: list[Any]) -> dict[str, Any]:
    """Re-serialize the assistant turn that requested tools (OpenAI wire shape)."""
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in tool_calls
        ],
    }
