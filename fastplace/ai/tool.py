"""@Tool — turn plain Python functions into LLM function-calling tools.

The decorator derives the OpenAI wire schema from type hints and a
Google-style ``Args:`` docstring section, registers the tool for agent
assembly, and leaves the function itself untouched and directly callable
(blueprint §9: "converts standard Python functions into structured JSON
schemas for LLM function calling using type hints and docstrings").
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args, get_origin

from pydantic import BaseModel, ConfigDict, create_model

#: What a tool handler may be — sync or async; the agent awaits when needed.
ToolFn = Callable[..., Any]


@dataclass(frozen=True)
class ToolSpec:
    """One registered tool: dispatch name, schema fragments, and handler."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: ToolFn

    def to_openai(self) -> dict[str, Any]:
        """The ``tools=[...]`` entry LiteLLM forwards to any provider."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


#: name → spec registry, populated by @Tool at import time.
tool_registry: dict[str, ToolSpec] = {}


def registered_tools() -> list[str]:
    """Sorted names of every registered tool."""
    return sorted(tool_registry)


def reset_tool_registry() -> None:
    """Clear the registry — test isolation."""
    tool_registry.clear()


def import_tools(project_root: str | Path | None = None) -> list[str]:
    """Import every module under ``app/ai/tools/`` so @Tool registrations run.

    Mirrors ``fastplace.queue.import_jobs``: the project root sits at the
    front of ``sys.path`` only for the call, cached ``app.*`` modules bound
    to a different root are evicted first (their registrations die with
    them), and an already-imported module is a cache hit — re-running the
    import in the same process never re-registers anything.
    """
    import sys

    root = Path(project_root) if project_root else Path.cwd()
    tools_dir = root / "app" / "ai" / "tools"
    if not tools_dir.is_dir():
        return []
    root_str = str(root)
    inserted = root_str not in sys.path
    if inserted:
        sys.path.insert(0, root_str)
    _evict_stale_app_modules(root)
    try:
        package = importlib.import_module("app.ai.tools")
        for module_info in pkgutil.iter_modules(package.__path__):
            if module_info.name.startswith("_"):
                continue
            importlib.import_module(f"app.ai.tools.{module_info.name}")
    except ModuleNotFoundError as exc:
        if exc.name not in ("app", "app.ai", "app.ai.tools"):
            raise
    finally:
        if inserted:
            sys.path.remove(root_str)
    return registered_tools()


def _evict_stale_app_modules(root: Path) -> None:
    """Drop cached ``app``/``app.*`` modules bound to a different root.

    Their @Tool registrations died with the modules — drop those too, or the
    fresh import collides on names the evicted handlers still "own". Mirrors
    the queue and vectors importers.
    """
    import sys

    evicted: set[str] = set()
    for name in list(sys.modules):
        if name != "app" and not name.startswith("app."):
            continue
        module = sys.modules.get(name)
        if module is None:
            continue
        origin = getattr(module, "__file__", None) or ""
        if not origin or not _path_contains(root, origin):
            sys.modules.pop(name, None)
            evicted.add(name)
    for tool_name in list(tool_registry):
        if tool_registry[tool_name].fn.__module__ in evicted:
            del tool_registry[tool_name]


def _path_contains(root: Path, origin: str) -> bool:
    """True when ``origin`` really lives under ``root`` (resolved-path containment)."""
    try:
        return Path(origin).resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False


def args_model(spec: ToolSpec) -> type[BaseModel]:
    """A pydantic model validating one call's arguments against the signature.

    Model-controlled JSON never reaches the handler unvalidated: unknown
    keys are forbidden (``extra="forbid"``) and every declared parameter is
    type-checked before dispatch. Built once and cached on the handler.
    """
    cached = getattr(spec.fn, "__fastplace_args_model__", None)
    if cached is not None:
        return cached

    sig = inspect.signature(spec.fn)
    hints = typing.get_type_hints(spec.fn, include_extras=False)
    fields: dict[str, tuple[Any, Any]] = {}
    for param_name, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        default = param.default if param.default is not inspect.Parameter.empty else ...
        fields[param_name] = (hints.get(param_name, Any), default)

    # pydantic ships no overload matching **fields + __config__ together
    model = create_model(  # type: ignore[call-overload]
        f"{spec.name}_arguments",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    spec.fn.__fastplace_args_model__ = model  # type: ignore[attr-defined]
    return model


class Tool:
    """Decorator marking a function as an agent tool.

    ``@Tool()`` names the tool after the function (falling back to the
    docstring summary when no description is given); ``@Tool(name=...)``
    pins a stable dispatch name. Names must fit the providers' wire
    alphabet — up to 64 letters, digits, ``_`` or ``-`` — so dotted module
    paths are rejected at decoration time, not by the provider at runtime.
    """

    #: The name pattern every function-calling provider accepts.
    NAME_PATTERN = re.compile(r"[a-zA-Z0-9_-]{1,64}")

    def __init__(self, name: str | None = None, description: str | None = None) -> None:
        self.name = name
        self.description = description

    def __call__(self, fn: ToolFn) -> ToolFn:
        hints = self._hints(fn)
        docstring = inspect.getdoc(fn) or ""
        parameters = _parameters_schema(fn, hints, docstring)
        description = self.description or _docstring_summary(docstring)
        if not description:
            raise ValueError(
                f"@Tool '{fn.__name__}' needs a description — pass one or add a "
                "docstring summary line"
            )
        name = self.name or fn.__name__
        if not self.NAME_PATTERN.fullmatch(name):
            raise ValueError(
                f"@Tool name '{name}' is invalid — providers only accept up to "
                "64 letters, digits, '_' or '-'"
            )
        if name in tool_registry:
            existing = tool_registry[name].fn
            raise ValueError(
                f"@Tool name collision: '{name}' is already registered by "
                f"{getattr(existing, '__module__', '?')}.{getattr(existing, '__qualname__', '?')}; "
                f"{fn.__module__}.{fn.__qualname__} must pass @Tool(name=...) to disambiguate"
            )
        spec = ToolSpec(name=name, description=description, parameters=parameters, fn=fn)
        tool_registry[name] = spec
        # Carry the spec on the function so agents resolve the exact tool even
        # after a registry reset re-imports things in a different order.
        fn.__fastplace_tool__ = spec  # type: ignore[attr-defined]
        return fn

    @staticmethod
    def _hints(fn: ToolFn) -> dict[str, Any]:
        try:
            return typing.get_type_hints(fn, include_extras=False)
        except Exception as exc:  # unresolvable forward refs etc.
            raise ValueError(f"@Tool '{fn.__name__}': cannot resolve type hints ({exc})") from exc


# ---------------------------------------------------------------------------
# schema derivation
# ---------------------------------------------------------------------------


def _docstring_summary(docstring: str) -> str:
    """First non-empty docstring line, stripped — the one-line description."""
    for line in docstring.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _parameters_schema(fn: ToolFn, hints: dict[str, Any], docstring: str) -> dict[str, Any]:
    """Build the OpenAI ``parameters`` object from annotations + docstring."""
    sig = inspect.signature(fn)
    descriptions = _parse_args_section(docstring)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param_name, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        hint = hints.get(param_name)
        if hint is None:
            raise ValueError(
                f"@Tool '{fn.__name__}': parameter '{param_name}' needs a type annotation "
                "(the JSON schema is derived from type hints)"
            )
        entry = _schema_for_type(hint)
        description = descriptions.get(param_name)
        if description:
            entry["description"] = description
        properties[param_name] = entry
        # Python itself demands the argument when there is no default — the
        # schema must agree, or the model may legally omit it and the
        # dispatch burns a round on a missing-argument error.
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _is_optional(hint: Any) -> bool:
    """Optional[X] in either spelling — typing.Union or PEP 604 ``X | None``."""
    origin = get_origin(hint)
    if origin is not typing.Union and origin is not types.UnionType:
        return False
    return type(None) in get_args(hint)


def _schema_for_type(hint: Any) -> dict[str, Any]:
    """Map one annotation to a JSON-schema fragment."""
    if _is_optional(hint):
        inner = next(arg for arg in get_args(hint) if arg is not type(None))
        return _schema_for_type(inner)

    origin = get_origin(hint)
    if origin is typing.Literal:
        values = list(get_args(hint))
        schema: dict[str, Any] = {"enum": values}
        # Only claim a "type" when every member agrees — bool first, since
        # isinstance(True, int) would mistype booleans as integers.
        kinds = {type(v) for v in values}
        if kinds == {bool}:
            schema["type"] = "boolean"
        elif kinds == {str}:
            schema["type"] = "string"
        elif kinds == {int}:
            schema["type"] = "integer"
        return schema
    if origin in (list, typing.List):  # noqa: UP006 — typing.List for py3.9 users
        args = get_args(hint)
        items = _schema_for_type(args[0]) if args else {}
        return {"type": "array", "items": items}
    if origin in (dict, typing.Dict):  # noqa: UP006
        return {"type": "object"}
    if origin is typing.Union or origin is types.UnionType:
        return {}  # non-Optional union — let the model try either shape
    if hint is list:  # bare list — element type left to the model
        return {"type": "array"}
    if hint is dict:  # bare dict — free-form object payload
        return {"type": "object"}

    if isinstance(hint, type) and issubclass(hint, BaseModel):
        # Pydantic models already speak JSON schema — $defs included, since
        # stripping them would leave dangling $ref pointers.
        return dict(hint.model_json_schema())

    simple: dict[type, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}
    if hint in simple:
        return {"type": simple[hint]}
    if hint is Any or hint is object:
        return {}
    raise ValueError(
        f"@Tool cannot map type '{getattr(hint, '__name__', hint)}' to a JSON schema — "
        "use str/int/float/bool, list[...], dict, Literal[...], a pydantic model, or Optional[...]"
    )


def _parse_args_section(docstring: str) -> dict[str, str]:
    """Extract ``param: description`` pairs from a Google-style Args section.

    Handles multi-line descriptions and the optional ``name (type):`` form.
    """
    descriptions: dict[str, str] = {}
    lines = docstring.splitlines()
    in_args = False
    current: str | None = None
    for line in lines:
        stripped = line.strip()
        if not in_args:
            if stripped.rstrip(":") in ("Args", "Arguments", "Parameters"):
                in_args = True
            continue
        if not stripped:
            continue
        indented = line != line.lstrip()
        if not indented:
            in_args = False  # a new top-level section (Returns:, Raises:, …)
            continue
        if current is not None and not _looks_like_param_line(stripped):
            # continuation of the previous parameter's description
            descriptions[current] = f"{descriptions[current]} {stripped}".strip()
            continue
        name, sep, rest = stripped.partition(":")
        if not sep:
            continue
        name = name.split("(")[0].strip()  # drop the optional "(type)" hint
        if not name.isidentifier():
            current = None
            continue
        descriptions[name] = rest.strip()
        current = name
    return descriptions


def _looks_like_param_line(stripped: str) -> bool:
    name = stripped.split(":", 1)[0].split("(", 1)[0].strip()
    return bool(name) and name.isidentifier()
