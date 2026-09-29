"""The SAQ queue dashboard — embedded behind one fail-closed auth guard.

``saq_web`` ships with zero authentication of its own, so the dashboard is
OFF by default and the whole mount is wrapped in a single pure-ASGI guard
that mirrors the ``auth`` middleware contract:

- anonymous **browser** navigations redirect to ``/login`` with the intended
  URL parked in the session (``request.intended()`` resumes after login);
- programmatic callers — the bridge header, or an ``Accept`` that asks for
  JSON without HTML — get the 401/403 JSON envelope, never a page;
- a parked two-factor challenge steers to the challenge page, not login;
- with ``QUEUE_DASHBOARD_ABILITY`` set, every request — including SAQ's own
  POST retry/abort routes — checks the gate first. An ability nobody
  defined surfaces the gate's ``ConfigurationError`` (fail loud); an
  undefined ability can never silently allow.

The guard reads ``scope["fastplace_user"]`` (populated by the global
ResolveUserMiddleware) and ``scope["session"]`` defensively: an app that
opts out of those middlewares gets the documented anonymous treatment.
The mount exists only when the flag is on **and** the queue driver is
``saq`` — a memory-driver process has no SAQ queue to show, so the mount
is skipped rather than crashed (``queue:health`` warns about the combo).
"""

from __future__ import annotations

import json
from typing import Any

from fastplace.auth.guards import TWO_FACTOR_CHALLENGE_KEY
from fastplace.auth.middleware import INTENDED_SESSION_KEY
from fastplace.config import config

DEFAULT_PATH = "/queue-dashboard"


def _settings() -> tuple[bool, str, str | None]:
    """(enabled, path, ability) from config — env overrides ride for free."""
    enabled = str(config("QUEUE_DASHBOARD_ENABLED", default=False)).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    path = str(config("QUEUE_DASHBOARD_PATH", default=DEFAULT_PATH)) or DEFAULT_PATH
    ability = config("QUEUE_DASHBOARD_ABILITY", default=None)
    ability = str(ability) if ability else None
    return enabled, path, ability


def dashboard_path() -> str:
    """The configured mount path (``QUEUE_DASHBOARD_PATH``)."""
    return _settings()[1]


def _wants_envelope(headers: list[tuple[bytes, bytes]]) -> bool:
    """Programmatic caller: bridge header, or JSON asked for without HTML."""
    bridge = accept = b""
    for name, value in headers:
        key = name.lower()
        if key == b"x-fastplace-request":
            bridge = value.lower()
        elif key == b"accept":
            accept = value.lower()
    if bridge == b"true":
        return True
    return b"application/json" in accept and b"text/html" not in accept


async def _send_json(send, status: int, message: str) -> None:
    body = json.dumps({"message": message}).encode()
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


async def _send_redirect(send, location: str) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 302,
            "headers": [(b"location", location.encode()), (b"content-length", b"0")],
        }
    )
    await send({"type": "http.response.body", "body": b""})


def build_dashboard_app(*, saq_queue: Any = None) -> Any | None:
    """The guarded SAQ web app, or ``None`` when the mount must not exist.

    ``None`` — not a stub, not an error — is the flag-off and memory-driver
    contract: nothing is mounted, so the path 404s exactly like any other
    unknown route. ``saq_queue`` injects the SAQ ``Queue`` object (the
    ``SaqQueue.queue`` seam); tests pass a fake, the kernel passes nothing
    and the process-wide queue is resolved lazily.
    """
    from fastplace.queue import SaqQueue
    from fastplace.queue import queue as process_queue

    enabled, path, ability = _settings()
    if not enabled:
        return None
    if str(config("QUEUE_DRIVER", default="memory")) != "saq":
        return None

    if saq_queue is None:
        process_saq_queue = process_queue()
        if not isinstance(process_saq_queue, SaqQueue):  # pragma: no cover — driver guard above
            return None
        saq_queue = process_saq_queue.queue  # lazy: no Redis until a command

    from saq.web.starlette import saq_web

    web = saq_web(path, [saq_queue])

    async def guarded(scope, receive, send):
        if scope["type"] != "http":
            await web(scope, receive, send)
            return

        user = scope.get("fastplace_user")
        if user is not None:
            if ability is not None:
                from fastplace.authz import gate

                # Undefined ability raises ConfigurationError here — loud,
                # on the first request, never a silent allow.
                if not await gate.allows(user, ability):
                    await _send_json(send, 403, "This action is unauthorized.")
                    return
            await web(scope, receive, send)
            return

        headers = [(bytes(k), bytes(v)) for k, v in scope.get("headers") or []]
        if _wants_envelope(headers):
            await _send_json(send, 401, "Unauthenticated.")
            return

        session = scope.get("session")
        if session is not None and session.get(TWO_FACTOR_CHALLENGE_KEY) is not None:
            await _send_redirect(send, "/two-factor-challenge")
            return

        full_path = scope.get("root_path", "") + scope.get("path", "")
        query = scope.get("query_string") or b""
        if query:
            full_path += "?" + query.decode("latin-1")
        if session is not None:
            session[INTENDED_SESSION_KEY] = full_path
        await _send_redirect(send, "/login")

    return guarded


def mount_dashboard(app) -> None:
    """Mount the guarded dashboard on ``app`` — a no-op when disabled."""
    dashboard = build_dashboard_app()
    if dashboard is not None:
        app.mount(dashboard_path(), dashboard)
