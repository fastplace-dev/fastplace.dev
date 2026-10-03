"""Browser-log HTTP sink — ``POST /_fastplace/browser-logs``.

A tiny raw-ASGI app mounted by the kernel when MCP browser-log capture is on
and the application runs with debug enabled. The browser-capture script
installed by the MCP guidelines POSTs console/error entries here; they land
in the process-wide ring buffer the ``browser-logs`` MCP tool drains. The
mount is a strict no-op otherwise, so the path 404s like any unknown route.
"""

from __future__ import annotations

import json

from fastplace.mcp.browser import get_buffer

BROWSER_LOG_MOUNT_PATH = "/_fastplace/browser-logs"
_MAX_BODY_BYTES = 1_000_000


def _flag_on(name: str, default: str = "0") -> bool:
    import os

    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


_CAPTURE_SCRIPT = """\
(function () {
  var ENDPOINT = "/_fastplace/browser-logs";
  var queue = [];
  var timer = null;

  function enqueue(level, message) {
    queue.push({ level: level, message: message, url: window.location.href });
    if (timer === null) timer = setTimeout(flush, 250);
  }

  function flush() {
    timer = null;
    if (queue.length === 0) return;
    var entries = queue;
    queue = [];
    fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entries: entries }),
      keepalive: true,
    }).catch(function () { /* capture must never break the page */ });
  }

  ["log", "info", "warn", "error", "debug"].forEach(function (level) {
    var original = console[level].bind(console);
    console[level] = function () {
      var args = Array.prototype.slice.call(arguments);
      original.apply(null, args);
      var text = args
        .map(function (a) {
          if (a instanceof Error) return a.stack || String(a);
          if (typeof a === "object") {
            try { return JSON.stringify(a); } catch (e) { return String(a); }
          }
          return String(a);
        })
        .join(" ");
      enqueue(level, text);
    };
  });

  window.addEventListener("error", function (event) {
    var error = event.error;
    enqueue(
      "error",
      error && error.stack ? error.stack : event.message + " (" + event.filename + ":" + event.lineno + ")"
    );
  });

  window.addEventListener("unhandledrejection", function (event) {
    var reason = event.reason;
    enqueue(
      "error",
      "Unhandled rejection: " + (reason && reason.stack ? reason.stack : String(reason))
    );
  });
})();
"""


def build_browser_log_app(*, buffer=None):
    """The sink ASGI app, or ``None`` when the mount must not exist."""
    if not _flag_on("FASTPLACE_MCP_BROWSER_LOGS"):
        return None
    # Dev-only capture: never sink browser logs in a production process.
    if not _flag_on("APP_DEBUG") and not _flag_on("APP_ENV_LOCAL", "0"):
        return None

    sink = buffer if buffer is not None else get_buffer()

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        if scope["method"] == "GET":
            await _send_js(send)
            return
        if scope["method"] != "POST":
            await _send_json(send, 405, {"error": "POST only"})
            return

        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        if len(body) > _MAX_BODY_BYTES:
            await _send_json(send, 413, {"error": "Payload too large"})
            return

        try:
            payload = json.loads(body or b"{}")
            entries = payload["entries"]
            if not isinstance(entries, list):
                raise TypeError("entries must be a list")
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("message"), str):
                    raise TypeError("each entry needs a string 'message'")
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            await _send_json(send, 400, {"error": f"Invalid payload: {exc}"})
            return

        accepted = 0
        for entry in entries:
            record = {"level": entry.get("level", "log"), "message": entry["message"]}
            if "url" in entry:
                record["url"] = entry["url"]
            sink.add(record)
            accepted += 1
        await _send_json(send, 200, {"accepted": accepted})

    return app


async def _send_js(send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/javascript; charset=utf-8"),
                (b"cache-control", b"no-store"),
                (b"content-length", str(len(_CAPTURE_SCRIPT.encode())).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _CAPTURE_SCRIPT.encode()})


async def _send_json(send, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def mount_browser_logs(app) -> None:
    """Mount the sink on the kernel app — a no-op when disabled."""
    sink = build_browser_log_app()
    if sink is not None:
        app.mount(BROWSER_LOG_MOUNT_PATH, sink)
