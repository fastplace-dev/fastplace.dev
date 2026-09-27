"""HTML error pages — the developer-facing face of unhandled exceptions.

JSON stays the contract for API clients and the SPA bridge; a browser
navigation (``Accept: text/html``) gets a styled page instead. Debug mode
renders the exception, the request, and the traceback; production renders
a generic page with no detail — the same policy the JSON handler follows.
"""

from __future__ import annotations

import html
import traceback
from typing import Any

from fastplace.http.response import Html

# Inline CSS keeps the page self-contained — no external request can fail
# while we are already handling a crash. Dark palette matches the framework
# theme's dark mode (zinc ground, emerald accent).
_PAGE_CSS = """
  :root { color-scheme: dark; }
  body {
    margin: 0; background: #0c0d10; color: #e4e4e7;
    font: 14px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .wrap { max-width: 960px; margin: 0 auto; padding: 40px 24px 64px; }
  .brand { color: #a1a1aa; letter-spacing: .08em; text-transform: uppercase;
           font-size: 12px; }
  h1 { margin: 8px 0 24px; font-size: 22px; color: #fafafa; }
  h1 .status { color: #f87171; }
  .card { background: #18181b; border: 1px solid #27272a; border-radius: 8px;
          padding: 16px 20px; margin-bottom: 16px; overflow-x: auto; }
  .card h2 { margin: 0 0 8px; font-size: 12px; color: #a1a1aa;
             text-transform: uppercase; letter-spacing: .08em; }
  .exception { color: #fbbf24; }
  pre { margin: 0; white-space: pre-wrap; word-break: break-word; }
  .frames pre { color: #d4d4d8; }
  .hint { color: #71717a; margin-top: 24px; font-size: 12px; }
  em { color: #34d399; font-style: normal; }
"""


def wants_html(request: Any) -> bool:
    """True when the caller is a browser navigation, not an API client.

    The SPA bridge (``X-Fastplace-Request``) always speaks JSON regardless
    of what ``Accept`` claims, and an explicit ``application/json`` beats
    a ``text/html`` entry in the same header.
    """
    headers = request.headers
    if str(headers.get("x-fastplace-request", "")).lower() == "true":
        return False
    # Media types are case-insensitive (RFC 9110) — normalize before matching.
    accept = str(headers.get("accept", "")).lower()
    return "text/html" in accept and "application/json" not in accept


def _query_stats_card(request: Any) -> str:
    """The card showing what the request executed before it died (may be empty)."""
    from fastplace.orm.instrumentation import request_query_stats

    stats = request_query_stats(request)
    if stats is None:
        return ""
    duplicates = stats.duplicates or {}
    lines = [
        f"{stats.statements} statements · {stats.slow_queries} slow · "
        f"{stats.total_seconds * 1000:.1f}ms total"
    ]
    for sql, count in list(duplicates.items())[:5]:
        preview = " ".join(sql.split())[:120]
        lines.append(f"x{count}  {preview}")
    if len(duplicates) > 5:
        lines.append(f"… and {len(duplicates) - 5} more repeated statements")
    rows = "\n".join(html.escape(line) for line in lines)
    return f'  <div class="card">\n    <h2>Query stats</h2>\n    <pre>{rows}</pre>\n  </div>\n'


def debug_error_page(request: Any, exc: Exception) -> Html:
    """Rich 500 page: exception, request line, query stats, and full traceback."""
    title = f"{type(exc).__name__}: {exc}"
    tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>500 Server Error — Fastplace</title>
<style>{_PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="brand">Fastplace</div>
  <h1><span class="status">500</span> Server Error</h1>

  <div class="card exception"><pre>{html.escape(title)}</pre></div>

  <div class="card">
    <h2>Request</h2>
    <pre>{html.escape(request.method)} {html.escape(str(request.url.path))}</pre>
  </div>

{_query_stats_card(request)}
  <div class="card frames">
    <h2>Traceback</h2>
    <pre>{html.escape(tb_text)}</pre>
  </div>

  <p class="hint">Debug mode is on — this page is never rendered in production.</p>
</div>
</body>
</html>"""
    return Html(body, status_code=500)


def production_error_page(request: Any) -> Html:
    """Generic 500 page — no exception detail ever leaves the server."""
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>500 Server Error — Fastplace</title>
<style>{_PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="brand">Fastplace</div>
  <h1><span class="status">500</span> Server Error</h1>

  <div class="card">
    <pre>Something went wrong on our side.</pre>
    <pre>The error has been logged. Please try again shortly.</pre>
  </div>

  <p class="hint"><em>→</em> <a href="/" style="color:#34d399;">Back to the application</a></p>
</div>
</body>
</html>"""
    return Html(body, status_code=500)


_STATUS_LABELS: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    409: "Conflict",
    410: "Gone",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    418: "I'm a teapot",
    419: "Page Expired",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


def _status_label(status_code: int) -> str:
    return _STATUS_LABELS.get(status_code, "Error")


def _iter_routes(routes: Any) -> list[tuple[str | None, set[str] | None]]:
    """Flatten the app's route tree (FastAPI wraps included routers)."""
    flat: list[tuple[str | None, set[str] | None]] = []
    for route in routes or []:
        nested = getattr(route, "original_router", None)
        if nested is not None:
            flat.extend(_iter_routes(nested.routes))
            continue
        flat.append((getattr(route, "path", None), getattr(route, "methods", None)))
    return flat


def _registered_routes_card(request: Any) -> str:
    """Debug orientation: the routes this app actually answers (capped)."""
    rows: list[str] = []
    app = getattr(request, "app", None)
    for path, methods in _iter_routes(getattr(app, "routes", None)):
        if not path or path.startswith(("/openapi", "/api/docs", "/docs/")):
            continue
        if methods:
            shown = ",".join(sorted(m for m in methods if m not in ("HEAD", "OPTIONS")))
            rows.append(f"{shown or 'ANY':8s} {path}")
        else:
            rows.append(f"{'MOUNT':8s} {path}")
        if len(rows) >= 30:
            rows.append("…")
            break
    if not rows:
        return ""
    body = "\n".join(html.escape(row) for row in rows)
    return (
        f'  <div class="card">\n    <h2>Registered routes</h2>\n    <pre>{body}</pre>\n  </div>\n'
    )


def http_error_page(
    request: Any,
    status_code: int,
    detail: str,
    *,
    debug: bool = False,
    headers: dict[str, str] | None = None,
) -> Html:
    """Styled page for HTTP error statuses shown to browser navigations.

    Replaces the bare ``{"message": ...}`` JSON a 404 navigation used to
    get: titled, ``lang``-attributed, consistent with the 500 pages. In
    debug mode it adds the registered-routes card so a mistyped URL is
    obvious at a glance.
    """
    label = _status_label(status_code)
    title = f"{status_code} {label} — Fastplace"
    detail_html = ""
    if detail and detail.strip() and str(detail) != str(status_code):
        detail_html = '  <div class="card"><pre>' + html.escape(str(detail)) + "</pre></div>\n"
    routes_card = _registered_routes_card(request) if debug else ""
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="brand">Fastplace</div>
  <h1><span class="status">{status_code}</span> {html.escape(label)}</h1>

{detail_html}  <div class="card">
    <pre>The page you are looking for could not be served.</pre>
  </div>

{routes_card}  <p class="hint"><em>→</em> <a href="/" style="color:#34d399;">Back to the application</a></p>
</div>
</body>
</html>"""
    return Html(body, status_code=status_code, headers=headers)


def boot_error_page(error_text: str, tb_text: str = "") -> Html:
    """The dev-shell 503: the backend failed to (re)load, this page refreshes.

    Served by ``fastplace.http.dev_shell`` when the project import raises.
    The meta refresh brings the browser back the moment the reloader
    recovers, so a mid-edit syntax error costs a spinner, not a hang.
    """
    tb_lines = [line for line in tb_text.splitlines() if line.strip()]
    tb_tail = "\n".join(tb_lines[-12:])
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="2">
<title>503 Backend failed to reload — Fastplace</title>
<style>{_PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="brand">Fastplace</div>
  <h1><span class="status">503</span> Backend failed to reload</h1>

  <div class="card exception"><pre>{html.escape(error_text)}</pre></div>

  <div class="card frames">
    <h2>Traceback</h2>
    <pre>{html.escape(tb_tail)}</pre>
  </div>

  <p class="hint">Fix the error and save — this page reloads automatically
  every 2 seconds. The full traceback is in your terminal.</p>
</div>
</body>
</html>"""
    return Html(body, status_code=503)
