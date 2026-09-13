"""Fastplace response construction — wraps Starlette response primitives."""

from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel
from starlette.responses import (
    FileResponse as File,
)
from starlette.responses import (
    HTMLResponse as Html,
)
from starlette.responses import (
    JSONResponse as Json,
)
from starlette.responses import (
    PlainTextResponse as Text,
)
from starlette.responses import (
    RedirectResponse as Redirect,
)
from starlette.responses import Response
from starlette.responses import (
    StreamingResponse as Stream,
)

__all__ = [
    "Response",
    "Json",
    "Html",
    "Text",
    "Redirect",
    "Stream",
    "File",
    "NoContent",
    "to_response",
]


def NoContent(headers: dict | None = None) -> Response:
    """204 No Content."""
    return Response(status_code=204, headers=headers or {})


def to_response(result: Any) -> Response:
    """Coerce any controller return value into a Starlette response.

    Accepts responses (pass-through), Pydantic models, dicts, lists, strings,
    bytes, and ``None`` (204).
    """
    if isinstance(result, Response):
        return result
    if isinstance(result, BaseModel):
        return Json(result.model_dump(mode="json"))
    if isinstance(result, (dict, list, tuple, bool, int, float)):
        return Json(_jsonable(result))
    if isinstance(result, str):
        return Text(result)
    if isinstance(result, bytes):
        return Response(content=result)
    if result is None:
        return NoContent()
    return Json(_jsonable(result))


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of common Python values to JSON-safe data."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return [_jsonable(v) for v in value]
    return value
