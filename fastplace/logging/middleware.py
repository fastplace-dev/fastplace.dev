"""RequestIdMiddleware — one correlation id per HTTP request.

The id is minted (or accepted) at the outermost edge, so every downstream
log line — routing, sessions, ORM instrumentation, error handling — carries
it, and the response echoes it so a client-side trace can be joined with
server-side logs. Pure ASGI, no framework coupling: correlation must work
before (and during) error responses, where higher-level middleware may
never run.

Trust policy: an inbound ``X-Request-ID`` is honored only when it matches
``[A-Za-z0-9-_]{1,64}`` — anything else (injection shapes, absurd lengths,
free text) is replaced with a fresh ``uuid4`` hex. The header is a hint,
never an authority.
"""

from __future__ import annotations

import re
import uuid

from fastplace.logging.context import _request_id, request_context

_REQUEST_ID_HEADER = b"x-request-id"
_VALID_ID = re.compile(r"^[A-Za-z0-9\-_]{1,64}$")

#: The header name applications use when reading/correlating programmatically.
REQUEST_ID_HEADER = "X-Request-ID"


def sanitize_request_id(raw: str) -> str:
    """Accept a caller-supplied id only in the documented shape, else ``""``."""
    candidate = raw.strip()
    return candidate if _VALID_ID.fullmatch(candidate) else ""


class RequestIdMiddleware:
    """Pure-ASGI correlation: contextvar in, response header out."""

    # ASGI callables are untyped by convention; the parameter stays loose.
    def __init__(self, app) -> None:  # type: ignore[no-untyped-def]
        self.app = app

    async def __call__(self, scope: dict, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = ""
        for name, value in scope.get("headers") or ():
            if name.lower() == _REQUEST_ID_HEADER:
                inbound = value.decode("latin-1")
                break
        request_id = sanitize_request_id(inbound) or uuid.uuid4().hex

        # The scope stamp outlives this middleware's finally-block: an
        # unhandled exception propagates past us to ServerErrorMiddleware,
        # which runs the kernel's 500 handler after the contextvar is
        # already reset — the handler re-enters the context from the scope
        # so the error log line stays correlated (same pattern as
        # fastplace_session_store).
        scope["fastplace_request_id"] = request_id

        token = _request_id.set(request_id)
        try:

            async def send_with_id(message: dict) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers") or [])
                    present = {name.lower() for name, _ in headers}
                    if _REQUEST_ID_HEADER not in present:
                        headers.append((_REQUEST_ID_HEADER, request_id.encode("ascii")))
                    message["headers"] = headers
                await send(message)

            await self.app(scope, receive, send_with_id)
        finally:
            _request_id.reset(token)


# Re-exported for one-stop import: the middleware owns the request half of
# correlation, so ``request_context`` travels with it for embedded hosts
# that build requests without ASGI.
__all__ = ["REQUEST_ID_HEADER", "RequestIdMiddleware", "request_context", "sanitize_request_id"]
