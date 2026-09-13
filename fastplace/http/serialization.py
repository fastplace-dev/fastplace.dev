"""Return-annotation validation for unified API controllers."""

from __future__ import annotations

import inspect
from functools import cache
from typing import Any, get_type_hints

from pydantic import BaseModel


@cache
def _cached_type_hints(handler: Any) -> dict[str, Any]:
    """Resolve a handler's type hints once — routes live for the process."""

    return get_type_hints(handler)


def validated_payload(handler: Any, result: Any) -> Any:
    """If ``handler`` annotates a Pydantic model return type, validate ``result``.

    This is the automatic response-schema mechanism: the controller's return
    annotation is the Pydantic v2 response schema, and the framework ensures
    the outbound JSON payload conforms to it.
    """
    try:
        hints = _cached_type_hints(handler)
    except Exception:  # pragma: no cover - unresolvable forward refs
        return result
    annotation = hints.get("return")
    if annotation is None or not (inspect.isclass(annotation) and issubclass(annotation, BaseModel)):
        return result
    if isinstance(result, annotation):
        return result
    if isinstance(result, BaseModel):
        return annotation.model_validate(result.model_dump(mode="json"))
    return annotation.model_validate(result)
