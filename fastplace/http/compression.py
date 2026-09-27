"""Response compression — gzip for large compressible bodies (serve-G4).

``fastplace serve`` is positioned as the whole server, so the kernel ships
a compression layer instead of assuming a reverse proxy will add one. Pure
ASGI on purpose: no Starlette BaseHTTPMiddleware (those buffer bodies and
break streaming).

Behavior:
- compresses only when the client offers gzip, the response media type is
  text-like (or JS/JSON/XML/SVG/WASM), nothing already set
  ``Content-Encoding``, and the body reaches ``minimum_size`` bytes;
- buffers only until the threshold decision is made — streamed bodies
  (including SSE, which is excluded by type) flow chunk-by-chunk after it;
- strips ``Content-Length`` and merges ``Vary: Accept-Encoding`` when
  compressing, leaving identity responses byte-for-byte untouched.
"""

from __future__ import annotations

import gzip
import io
from typing import Any

# Media types worth compressing. Anything binary (png, woff2, mp4, …) is
# excluded on purpose: already-compressed payloads grow slightly and burn
# CPU for nothing. text/event-stream (SSE) is excluded because buffering
# or framing it would break the stream contract.
_COMPRESSIBLE_TYPES = {
    "text/plain",
    "text/html",
    "text/css",
    "text/javascript",
    "text/xml",
    "application/javascript",
    "application/json",
    "application/xml",
    "image/svg+xml",
    "application/wasm",
}

_NO_BODY_STATUSES = frozenset({204, 304})

_DECIDING, _PASSTHROUGH, _COMPRESSING = "deciding", "passthrough", "compressing"


class CompressionMiddleware:
    """Gzip responses over ``minimum_size`` bytes for clients that accept it."""

    def __init__(self, app: Any, minimum_size: int = 1024, compresslevel: int = 6) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        accept_encoding = b""
        for key, value in scope.get("headers", []):
            if key.lower() == b"accept-encoding":
                accept_encoding = value
                break
        if b"gzip" not in accept_encoding.lower():
            await self.app(scope, receive, send)
            return
        responder = _CompressingResponder(
            send, minimum_size=self.minimum_size, compresslevel=self.compresslevel
        )
        await self.app(scope, receive, responder)


class _CompressingResponder:
    """One decision, then a committed path: identity passthrough or gzip.

    ``http.response.start`` is held back only while the size threshold is
    still undecided; once compression commits, chunks stream straight
    through the ``GzipFile`` so long responses are never fully buffered.
    """

    def __init__(self, send: Any, *, minimum_size: int, compresslevel: int) -> None:
        self._send = send
        self._minimum_size = minimum_size
        self._compresslevel = compresslevel
        self._mode = _DECIDING
        self._start: dict[str, Any] | None = None
        self._candidate = False
        self._buffer = bytearray()
        self._gzip: gzip.GzipFile | None = None
        self._gzip_buf: io.BytesIO | None = None
        self._gzip_sent = 0

    async def __call__(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            self._evaluate_start(message)
            if self._mode == _PASSTHROUGH:
                await self._send(message)
            return
        if message["type"] != "http.response.body":
            await self._send(message)
            return
        body: bytes = message.get("body", b"") or b""
        more = bool(message.get("more_body", False))

        if self._mode == _PASSTHROUGH:
            await self._send(message)
            return
        if self._mode == _COMPRESSING:
            await self._send_compressed(body, more=more)
            return

        self._buffer += body
        if not more:
            # Complete body in hand — decide with exact knowledge.
            if self._candidate and len(self._buffer) >= self._minimum_size:
                await self._begin_compression()
                await self._send_compressed(bytes(self._buffer), more=False)
            else:
                await self._send_identity()
            return
        if self._candidate and len(self._buffer) >= self._minimum_size:
            await self._begin_compression()
            await self._send_compressed(bytes(self._buffer), more=True)
            self._buffer.clear()
        # Below threshold with more chunks coming: keep holding — the
        # identity fallback must stay byte-exact, header and all.

    def _evaluate_start(self, message: dict[str, Any]) -> None:
        self._start = message
        content_type = b""
        has_encoding = False
        for key, value in message.get("headers") or []:
            lowered = key.lower()
            if lowered == b"content-type":
                content_type = value
            elif lowered == b"content-encoding":
                has_encoding = True
        if has_encoding or message.get("status", 200) in _NO_BODY_STATUSES:
            self._mode = _PASSTHROUGH
            return
        media_type = content_type.split(b";")[0].strip().decode("latin-1").lower()
        self._candidate = media_type in _COMPRESSIBLE_TYPES
        if not self._candidate:
            self._mode = _PASSTHROUGH

    async def _send_identity(self) -> None:
        assert self._start is not None
        await self._send(self._start)
        await self._send(
            {"type": "http.response.body", "body": bytes(self._buffer), "more_body": False}
        )
        self._buffer.clear()

    async def _begin_compression(self) -> None:
        assert self._start is not None
        headers = [
            (key, value)
            for key, value in self._start.get("headers") or []
            if key.lower() not in (b"content-length", b"content-encoding", b"vary")
        ]
        headers.append((b"content-encoding", b"gzip"))
        headers.append((b"vary", b"Accept-Encoding"))
        self._start["headers"] = headers
        self._mode = _COMPRESSING
        await self._send(self._start)

    async def _send_compressed(self, data: bytes, *, more: bool) -> None:
        chunk = self._gzip_write(data)
        if not more and self._gzip is not None:
            self._gzip.close()
            chunk += self._gzip_buf.getvalue()[self._gzip_sent :]  # type: ignore[union-attr]
        await self._send({"type": "http.response.body", "body": chunk, "more_body": more})

    def _gzip_write(self, data: bytes) -> bytes:
        if self._gzip is None:
            self._gzip_buf = io.BytesIO()
            self._gzip = gzip.GzipFile(
                mode="wb",
                compresslevel=self._compresslevel,
                fileobj=self._gzip_buf,
            )
        self._gzip.write(data)
        assert self._gzip_buf is not None
        out = self._gzip_buf.getvalue()
        fresh = out[self._gzip_sent :]
        self._gzip_sent = len(out)
        return fresh
