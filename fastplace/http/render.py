"""render() — the server-driven SPA bridge response (Inertia pattern).

Initial load returns an HTML document with the container element and the
initial JSON page payload; subsequent SPA navigation sends
``X-Fastplace-Request: true`` and receives only the JSON page payload.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from fastplace.http.flash import ERRORS_FLASH_KEY, FLASH_SESSION_KEY
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
    {noscript}
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
    # Identity dedupe — repeated boots (dev reload, create_app in tests)
    # must not stack the same callback.
    if callback not in _shared_props:
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
            # Flashed validation errors (no-JS redirect-back) surface the
            # same one-shot way — page props win over the flashed map.
            errors = session.pop(ERRORS_FLASH_KEY, None)
            if errors:
                props.setdefault("errors", errors)
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
    title: str | None = None,
    description: str | None = None,
    canonical: str | None = None,
    image: str | None = None,
    robots: str | None = None,
    og: dict[str, str] | None = None,
    head_tags: list[str] | None = None,
) -> Response:
    """Return the bridge response for a hydrated React page.

    The head keyword arguments (title, description, canonical, image,
    robots, og, head_tags) shape the initial HTML document only — crawlers
    and link unfurlers read the head without executing JavaScript, so every
    tag must already be present server-side. Bridge (JSON) responses ignore
    them. URLs are absolutized against ``APP_URL`` config, never the
    request's Host header.
    """
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

    page_title = title if title is not None else _title_for(component)
    document = _HTML_SHELL.format(
        title=html.escape(page_title),
        assets=_assets(request),
        page=html.escape(json.dumps(payload, ensure_ascii=False), quote=True),
        noscript=_noscript_block(page_title),
    )

    injection = "".join(
        f"    {tag}\n"
        for tag in _head_extra_tags(
            page_title=page_title,
            description=description,
            canonical=canonical,
            image=image,
            robots=robots,
            og=og,
            head_tags=head_tags,
        )
    )
    if csrf_token:
        # The React bridge reads this tag and echoes the token back on
        # unsafe-method visits (X-Fastplace-CSRF-Token).
        injection += f'    <meta name="csrf-token" content="{html.escape(csrf_token)}">\n'
    if injection:
        document = document.replace("</head>", injection + "</head>", 1)
    return Html(
        document,
        status_code=status,
        headers={
            **(headers or {}),
            "Vary": _BRIDGE_HEADER,
        },
    )


def _head_extra_tags(
    *,
    page_title: str,
    description: str | None,
    canonical: str | None,
    image: str | None,
    robots: str | None,
    og: dict[str, str] | None,
    head_tags: list[str] | None,
) -> list[str]:
    """Head tags derived from the render() keyword surface.

    Order: description, robots, canonical, then the Open Graph block and
    twitter:card, then the caller's raw ``head_tags``. The csrf-token meta
    is appended after all of these by render().
    """
    og = og or {}
    tags: list[str] = []

    if description:
        tags.append(f'<meta name="description" content="{html.escape(description)}">')
    if robots:
        tags.append(f'<meta name="robots" content="{html.escape(robots)}">')

    canonical_url = _absolute_url(canonical)
    if canonical and canonical_url:
        tags.append(f'<link rel="canonical" href="{html.escape(canonical_url)}">')

    # Open Graph. og:title/og:type/og:site_name always emit (a share card
    # needs the minimum); the rest only when a value resolves. og:url must
    # carry the same value as the canonical link.
    og_title = og.get("title", page_title)
    og_type = og.get("type", "website")
    site_name = str(og.get("site_name") or _app_name())
    og_description = og.get("description", description)
    og_url = _absolute_url(og.get("url") or canonical)
    og_image = _absolute_url(og.get("image") or image)

    tags.append(f'<meta property="og:title" content="{html.escape(og_title)}">')
    tags.append(f'<meta property="og:type" content="{html.escape(og_type)}">')
    tags.append(f'<meta property="og:site_name" content="{html.escape(site_name)}">')
    if og_description:
        tags.append(f'<meta property="og:description" content="{html.escape(og_description)}">')
    if og_url:
        tags.append(f'<meta property="og:url" content="{html.escape(og_url)}">')
    if og_image:
        tags.append(f'<meta property="og:image" content="{html.escape(og_image)}">')
    card = "summary_large_image" if og_image else "summary"
    tags.append(f'<meta name="twitter:card" content="{card}">')

    # Unknown og keys pass through escaped (og:locale, og:video, ...) —
    # sorted so output is deterministic.
    known = {"title", "description", "image", "url", "type", "site_name"}
    for key in sorted(og):
        if key not in known:
            tags.append(f'<meta property="og:{html.escape(key)}" content="{html.escape(og[key])}">')

    if head_tags:
        # Trusted developer markup, appended verbatim.
        tags.extend(head_tags)
    return tags


def _absolute_url(value: str | None) -> str | None:
    """Absolutize ``value`` against APP_URL; pass absolute URLs through.

    Relative URLs without a resolvable base are dropped (fail safe) — the
    canonical base comes from config, never from the request's Host header,
    which an attacker can poison.
    """
    if not value:
        return None
    if value.startswith(("http://", "https://")):
        return value
    from fastplace.config import config

    base = str(config("APP_URL", default="") or "")
    if not base:
        return None
    return f"{base.rstrip('/')}/{value.lstrip('/')}"


def _app_name() -> str:
    from fastplace.config import config

    return str(config("APP_NAME", default="Fastplace") or "Fastplace")


def _noscript_block(page_title: str) -> str:
    """The unconditional no-JS fallback card rendered before the mount div.

    Inline styles only — Tailwind never sees this template, so its classes
    would be purged from the production stylesheet. The card uses a fixed
    dark palette readable against both theme backgrounds.
    """
    return (
        "<noscript>\n"
        '  <div style="margin:24px auto;max-width:560px;padding:20px 24px;'
        "border:1px solid #3f3f46;border-radius:12px;background:#18181b;color:#fafafa;"
        'font-family:system-ui,sans-serif;font-size:14px;line-height:1.6">\n'
        f'    <p style="margin:0 0 8px;font-weight:600">'
        f"{html.escape(_app_name())} — {html.escape(page_title)}</p>\n"
        '    <p style="margin:0">This page needs JavaScript for the full interface. '
        "Forms still submit without it: posting a form reloads the page with the "
        "result.</p>\n"
        "  </div>\n"
        "</noscript>"
    )


def _title_for(component: str) -> str:
    tail = component.rsplit("/", 1)[-1]
    words = tail.replace("-", " ").replace("_", " ")
    # Split camelCase boundaries so "ForgotPassword" reads as two words.
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", words)
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
