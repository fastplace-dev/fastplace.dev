"""render() — the server-driven SPA bridge response (Inertia pattern).

Initial load returns an HTML document with the container element and the
initial JSON page payload; subsequent SPA navigation sends
``X-Fastplace-Request: true`` and receives only the JSON page payload.
"""

from __future__ import annotations

import html
import json
from typing import Any

from pydantic import BaseModel

from fastplace.http.request import Request
from fastplace.http.response import Html, Json, Response

_BRIDGE_HEADER = "X-Fastplace-Request"

_HTML_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>
    {assets}
</head>
<body>
    <div id="fastplace" data-page="{page}"></div>
</body>
</html>"""


def page_payload(request: Request, component: str, props: Any) -> dict:
    """Build the JSON page payload shared by both render modes."""
    if isinstance(props, BaseModel):
        props = props.model_dump(mode="json")
    return {
        "component": component,
        "props": props or {},
        "url": request.full_path,
        "version": _asset_version(),
    }


def render(
    request: Request,
    *,
    component: str,
    props: Any = None,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> Response:
    """Return the bridge response for a hydrated React page."""
    payload = page_payload(request, component, props)

    if request.is_bridge:
        return Json(payload, status_code=status, headers={
            **(headers or {}),
            "Vary": _BRIDGE_HEADER,
        })

    document = _HTML_SHELL.format(
        title=_title_for(component),
        assets=_assets(request),
        page=html.escape(json.dumps(payload, ensure_ascii=False), quote=True),
    )
    return Html(document, status_code=status, headers={
        **(headers or {}),
        "Vary": _BRIDGE_HEADER,
    })


def _title_for(component: str) -> str:
    tail = component.rsplit("/", 1)[-1]
    words = tail.replace("-", " ").replace("_", " ")
    return words.strip().title() or "Fastplace"


def _assets(request: Request) -> str:
    from fastplace.config import config
    from fastplace.http.assets import asset_tags

    app = request.starlette.scope.get("app")
    root = getattr(getattr(app, "state", None), "fastplace_root", None) or config(
        "FASTPLACE_ROOT", default="."
    )
    return asset_tags(
        root,
        vite_dev_url=config("VITE_DEV_URL"),
        app_env=str(config("APP_ENV", default="production")),
    )


def _asset_version() -> str:
    from fastplace.config import config

    return str(config("ASSET_VERSION", default="") or "")
