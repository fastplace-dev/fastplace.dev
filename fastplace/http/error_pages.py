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


def debug_error_page(request: Any, exc: Exception) -> Html:
    """Rich 500 page: exception, request line, and full traceback."""
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
