"""Maintenance mode — the 503 gate and its state file (spec #61).

``fastplace down`` writes ``storage/framework/maintenance.json`` under the
project root; the kernel installs ``MaintenanceMiddleware`` outermost, so a
down application answers with a styled 503 page before sessions mint
cookies or the bridge surface is reached. Operators keep browsing through a
``?secret=<value>`` query param (or an equivalent cookie) to verify a deploy
mid-maintenance. ``fastplace up`` removes the file and the gate re-opens.
"""

from __future__ import annotations

import hmac
import html
import json
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from starlette.datastructures import Headers

#: Where `down` writes its state, relative to the project root. One constant,
#: shared by the CLI writer and the middleware reader — never duplicated.
MAINTENANCE_FILE = "storage/framework/maintenance.json"

#: Cookie an operator may plant to keep browsing during maintenance — same
#: value the ``?secret=`` query param carries. The middleware only ever
#: READS it; setting the cookie is the operator's choice, never ours.
SECRET_COOKIE = "fastplace_maintenance"

# The 503 short-circuits outside the kernel's security-headers middleware,
# so the baseline hardening is mirrored here (values match
# kernel._SECURITY_HEADERS — keep the two in sync).
_HARDENING_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)

# Inline CSS keeps the page self-contained — no external request can fail
# while the app is down. Dark palette matches the framework theme's dark
# mode (zinc ground), the same family as the error pages.
_PAGE_CSS = """
  :root { color-scheme: dark; }
  body {
    margin: 0; background: #0c0d10; color: #e4e4e7;
    font: 14px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
    display: flex; min-height: 100vh; align-items: center; justify-content: center;
  }
  .wrap { max-width: 420px; padding: 40px 24px; text-align: center; }
  .brand { color: #a1a1aa; letter-spacing: .08em; text-transform: uppercase;
           font-size: 12px; }
  h1 { margin: 8px 0 12px; font-size: 22px; color: #fafafa; }
  p { margin: 0; color: #a1a1aa; }
"""


def is_down(root: str | Path) -> dict[str, Any] | None:
    """The parsed maintenance state when down, ``None`` when up.

    A present-but-unparseable file stays DOWN with an empty state — the
    operator asked for maintenance, and a corrupt file must not silently
    re-open the application.
    """
    try:
        raw = (Path(root) / MAINTENANCE_FILE).read_text()
    except OSError:
        return None
    try:
        state = json.loads(raw)
    except ValueError:
        return {}
    return state if isinstance(state, dict) else {}


def _maintenance_page(state: dict[str, Any]) -> str:
    """The styled 503 document. State values are operator input — escaped."""
    refresh = state.get("refresh")
    meta = (
        f'<meta http-equiv="refresh" content="{html.escape(str(refresh), quote=True)}">'
        if refresh
        else ""
    )
    retry = state.get("retry")
    when = f"back in {html.escape(str(retry))} seconds" if retry else "back shortly"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
{meta}
<title>Maintenance</title>
<style>{_PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
<div class="brand">Fastplace</div>
<h1>Maintenance</h1>
<p>We're making a few changes — {when}.</p>
</div>
</body>
</html>"""


class MaintenanceMiddleware:
    """Pure-ASGI 503 gate — reads the state file once per request.

    Absent file: passthrough with no further work. Present file: everything
    gets the 503 page unless the request carries the state's ``secret``
    (``?secret=`` query param or the :data:`SECRET_COOKIE` cookie). The
    middleware never writes a bypass cookie itself.
    """

    def __init__(self, app: Any, *, root: str | Path) -> None:
        self.app = app
        self.root = Path(root)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = is_down(self.root)
        if state is None or self._bypassed(scope, state):
            await self.app(scope, receive, send)
            return
        await self._send_503(state, send)

    def _bypassed(self, scope: dict, state: dict[str, Any]) -> bool:
        """True when the request presented the configured secret."""
        secret = state.get("secret")
        if not secret:
            return False  # no secret configured -> nothing bypasses
        supplied = self._query_secret(scope) or self._cookie_secret(scope)
        return bool(supplied) and hmac.compare_digest(str(supplied), str(secret))

    def _query_secret(self, scope: dict) -> str | None:
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        values = query.get("secret")
        return values[0] if values else None

    def _cookie_secret(self, scope: dict) -> str | None:
        cookie: SimpleCookie = SimpleCookie()
        try:
            cookie.load(Headers(scope=scope).get("cookie", ""))
        except Exception:  # malformed cookie header -> no bypass via cookie
            return None
        morsel = cookie.get(SECRET_COOKIE)
        return morsel.value if morsel and morsel.value else None

    async def _send_503(self, state: dict[str, Any], send: Any) -> None:
        body = _maintenance_page(state).encode("utf-8")
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"text/html; charset=utf-8"),
            # A status page must never be cached by intermediaries.
            (b"cache-control", b"no-store"),
            *_HARDENING_HEADERS,
        ]
        retry = state.get("retry")
        if retry is not None:
            headers.append((b"retry-after", str(retry).encode("latin-1")))
        await send({"type": "http.response.start", "status": 503, "headers": headers})
        await send({"type": "http.response.body", "body": body})
