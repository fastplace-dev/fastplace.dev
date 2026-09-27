"""Response compression (serve-G4) — gzip for large compressible bodies.

The CLI positions ``fastplace serve`` as the whole server, so the kernel
ships a compression layer: gzip when the client offers it, the body is
compressible and at least ``minimum_size`` bytes, and nothing already
encoded it. Streaming/SSE and binary types pass through untouched.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from fastplace.http import Html, Router, get_app
from fastplace.http.compression import CompressionMiddleware

BIG = "fastplace " * 400  # ~3.6 KB, comfortably past the 1 KB threshold
SMALL = "tiny"


def _client_with(routes=None, debug_config=None):
    app = get_app(routes=routes, config=debug_config or {"APP_DEBUG": False})
    return TestClient(app)


def test_large_text_response_is_gzipped():
    routes = Router()

    async def page(request):  # noqa: ANN001
        return Html(BIG)

    routes.get("/page", page)
    with _client_with(routes) as client:
        resp = client.get("/page", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    assert resp.text == BIG  # transparently decoded, body intact
    assert "accept-encoding" in resp.headers.get("vary", "").lower()


def test_small_response_passes_through():
    routes = Router()

    async def page(request):  # noqa: ANN001
        return Html(SMALL)

    routes.get("/page", page)
    with _client_with(routes) as client:
        resp = client.get("/page", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers
    assert resp.text == SMALL


def test_client_without_gzip_gets_identity():
    routes = Router()

    async def page(request):  # noqa: ANN001
        return Html(BIG)

    routes.get("/page", page)
    with _client_with(routes) as client:
        resp = client.get("/page", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in resp.headers
    assert resp.text == BIG


def test_binary_types_are_not_compressed():
    from fastplace.http import Response

    routes = Router()

    async def image(request):  # noqa: ANN001
        return Response(
            b"\x89PNG\r\n\x1a\n" + b"\x00\xff\x00\xff" * 600,
            status_code=200,
            media_type="image/png",
        )

    routes.get("/img", image)
    with _client_with(routes) as client:
        resp = client.get("/img", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers


def test_sse_stream_is_never_buffered_or_compressed():
    from collections.abc import AsyncIterator

    from fastplace.http import Stream

    routes = Router()

    async def events(request):  # noqa: ANN001
        async def gen() -> AsyncIterator[bytes]:
            for i in range(20):
                yield f"data: tick {i}\n\n".encode()

        return Stream(gen(), status_code=200, media_type="text/event-stream")

    routes.get("/events", events)
    with _client_with(routes) as client:
        resp = client.get("/events", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in resp.headers
    assert resp.text.count("data: tick") == 20


def test_pre_encoded_response_is_left_alone():
    import gzip as gzip_mod

    from fastplace.http import Response

    routes = Router()

    async def prezip(request):  # noqa: ANN001
        payload = gzip_mod.compress(BIG.encode())
        return Response(
            payload, status_code=200, media_type="text/plain", headers={"Content-Encoding": "gzip"}
        )

    routes.get("/prezip", prezip)
    with _client_with(routes) as client:
        resp = client.get("/prezip", headers={"Accept-Encoding": "gzip"})
    assert resp.headers.get("content-encoding") == "gzip"
    # httpx transparently decodes; the point is the body survived a single
    # gzip layer — not double-compressed into garbage.
    assert resp.text == BIG


def test_streamed_large_body_compression_stays_complete():
    from collections.abc import AsyncIterator

    from fastplace.http import Stream

    routes = Router()

    async def stream(request):  # noqa: ANN001
        async def gen() -> AsyncIterator[bytes]:
            for _ in range(10):
                yield (BIG + "\n").encode()

        return Stream(gen(), status_code=200, media_type="text/plain")

    routes.get("/stream", stream)
    with _client_with(routes) as client:
        resp = client.get("/stream", headers={"Accept-Encoding": "gzip"})
    expected = (BIG + "\n") * 10
    # The whole body crosses the threshold on chunk 1 — compression must
    # commit, not quietly fall back to identity.
    assert resp.headers.get("content-encoding") == "gzip"
    assert resp.text == expected


def test_compression_merges_app_set_vary_tokens():
    # render() marks bridge responses `Vary: X-Fastplace-Request`; the
    # middleware must add Accept-Encoding alongside, never clobber it —
    # dropping it would let a shared cache serve a bridge JSON payload to
    # a browser navigation.
    routes = Router()

    async def page(request):  # noqa: ANN001
        return Html(BIG, headers={"Vary": "X-Fastplace-Request"})

    routes.get("/page", page)
    with _client_with(routes) as client:
        resp = client.get("/page", headers={"Accept-Encoding": "gzip"})
    assert resp.headers.get("content-encoding") == "gzip"
    vary = resp.headers.get("vary", "")
    tokens = {token.strip().lower() for token in vary.split(",")}
    assert "accept-encoding" in tokens
    assert "x-fastplace-request" in tokens


def test_gzip_sink_drains_bounded():
    # The compressor's write target must hand back and forget each batch —
    # a getvalue()-per-chunk design re-copies the whole payload every flush
    # (quadratic) and retains it all (unbounded).
    from fastplace.http.compression import _GzipSink

    sink = _GzipSink()
    sink.write(b"abc")
    sink.write(b"de")
    assert sink.drain() == b"abcde"
    sink.write(b"fg")
    assert sink.drain() == b"fg"
    assert sink.drain() == b""


def test_middleware_works_standalone():
    from fastapi import FastAPI

    app = FastAPI()
    app.add_middleware(CompressionMiddleware)

    @app.get("/big")
    async def big():
        return Html(BIG)

    with TestClient(app) as client:
        resp = client.get("/big", headers={"Accept-Encoding": "gzip"})
    assert resp.headers.get("content-encoding") == "gzip"
    assert resp.text == BIG
