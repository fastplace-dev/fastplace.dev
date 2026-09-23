"""render() — the server-driven SPA bridge response (Inertia pattern).

Initial load returns an HTML document with the container element and the
initial JSON page payload; subsequent SPA navigation sends
``X-Fastplace-Request: true`` and receives only the JSON page payload.
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from fastplace.http.flash import FLASH_SESSION_KEY
from fastplace.http.request import Request
from fastplace.http.response import Html, Json, Response

_BRIDGE_HEADER = "X-Fastplace-Request"

# The pre-paint block between the marker comments must stay byte-identical to
# the copy in the repo-root index.html (tests/http/test_render.py enforces the
# lockstep). Because this shell is a str.format() template, every literal
# brace inside the block is doubled here.
_HTML_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>
    <!-- fastplace-appearance-prepaint -->
    <script>
      (function () {{
        var mode = "system";
        try {{
          var stored = localStorage.getItem("fastplace-appearance");
          if (stored === "light" || stored === "dark") mode = stored;
        }} catch (e) {{}}
        var dark =
          mode === "dark" ||
          (mode !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
        var root = document.documentElement;
        if (mode === "light" || mode === "dark") root.setAttribute("data-theme", mode);
        else root.removeAttribute("data-theme");
        if (dark) root.classList.add("dark");
        root.style.colorScheme = dark ? "dark" : "light";
      }})();
    </script>
    <style>
      html {{
        background-color: oklch(0.985 0.005 250);
      }}
      html.dark {{
        background-color: oklch(0.19 0.02 262);
      }}
    </style>
    <!-- /fastplace-appearance-prepaint -->
    {assets}
</head>
<body>
    <div id="fastplace" data-page="{page}"></div>
</body>
</html>"""


SharedPropsCallback = Callable[[Request], dict[str, Any] | None]

#: Registered shared-props callbacks merged into every page payload (§4.16).
_shared_props: list[SharedPropsCallback] = []


def share(callback: SharedPropsCallback) -> None:
    """Register a callback contributing shared props to every page payload.

    Page-specific props win: shared values merge with ``setdefault``
    semantics. Later phases register ``auth.user`` and flash/status
    channels here.
    """
    _shared_props.append(callback)


def reset_shared_props() -> None:
    """Drop every shared-props registration — tests and config reloads."""
    _shared_props.clear()


def page_payload(request: Request, component: str, props: Any) -> dict:
    """Build the JSON page payload shared by both render modes."""
    if isinstance(props, BaseModel):
        props = props.model_dump(mode="json")
    props = props or {}
    if isinstance(props, dict):
        # Shared channels first (auth.user, flash, status) — page props and
        # the CSRF token below always win over shared contributions.
        for callback in _shared_props:
            extra = callback(request)
            if isinstance(extra, dict):
                for key, value in extra.items():
                    if key != "csrf_token":
                        props.setdefault(key, value)
        # One-shot flash channel: the value survives the redirect (only this
        # pop consumes it) and page props win over the flashed status.
        try:
            session = request.session
        except Exception:
            session = None
        if isinstance(session, dict):
            value = session.pop(FLASH_SESSION_KEY, None)
            if value is not None:
                props.setdefault("status", value)
        # No-JS form posts cannot read the <meta> tag — every page's props
        # carry the session CSRF token so hidden ``_token`` inputs can use it.
        token = _session_csrf_token(request)
        if token:
            props.setdefault("csrf_token", token)
    return {
        "component": component,
        "props": props,
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
    csrf_token = _session_csrf_token(request)

    if request.is_bridge:
        return Json(
            payload,
            status_code=status,
            headers={
                **(headers or {}),
                "Vary": _BRIDGE_HEADER,
            },
        )

    document = _HTML_SHELL.format(
        title=_title_for(component),
        assets=_assets(request),
        page=html.escape(json.dumps(payload, ensure_ascii=False), quote=True),
    )
    if csrf_token:
        # The React bridge reads this tag and echoes the token back on
        # unsafe-method visits (X-Fastplace-CSRF-Token).
        document = document.replace(
            "</head>",
            f'    <meta name="csrf-token" content="{html.escape(csrf_token)}">\n</head>',
            1,
        )
    return Html(
        document,
        status_code=status,
        headers={
            **(headers or {}),
            "Vary": _BRIDGE_HEADER,
        },
    )


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


def _session_csrf_token(request: Request) -> str | None:
    """The CSRF token from the request's session, when one is active."""
    try:
        session = request.session
    except Exception:
        return None
    if not isinstance(session, dict):
        return None
    token = session.get("_token")
    return token if isinstance(token, str) and token else None
