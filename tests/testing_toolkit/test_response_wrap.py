"""TestResponse.wrap must survive gzip-compressed transport responses.

The ASGI transport materializes a response whose body it then decodes —
``response.content`` is plain again while ``Content-Encoding: gzip`` stays in
``response.headers``. ``wrap()`` rebuilds an ``httpx.Response`` from those two
halves, so it must not hand httpx a decode instruction for bytes that are
already decoded (any page over the compression threshold crashes with
``zlib.error: incorrect header check`` otherwise).
"""

from __future__ import annotations

import gzip

import httpx

from fastplace.testing.client import TestResponse


def _materialized_gzip_response() -> httpx.Response:
    """The response state after the transport read it: decoded body, raw headers."""
    body = b"<!DOCTYPE html><html>" + b"x" * 4096 + b"</html>"
    response = httpx.Response(
        200,
        headers={
            "content-encoding": "gzip",
            "content-length": "1",  # stale on purpose — wrap must not keep it
            "content-type": "text/html; charset=utf-8",
        },
        content=gzip.compress(body),
    )
    assert response.content == body  # httpx decoded it on construction
    response.request = httpx.Request("GET", "http://test/page")
    return response


def test_wrap_keeps_decoded_content_readable():
    wrapped = TestResponse.wrap(_materialized_gzip_response())

    assert wrapped.status_code == 200
    assert wrapped.content.startswith(b"<!DOCTYPE html>")
    assert wrapped.text.endswith("</html>")


def test_wrap_drops_stale_content_length():
    wrapped = TestResponse.wrap(_materialized_gzip_response())

    # The decoded body is bigger than the compressed one; httpx re-derives
    # the length from the bytes actually carried, so it must agree.
    assert wrapped.headers["content-length"] == str(len(wrapped.content))
    assert wrapped.headers["content-type"] == "text/html; charset=utf-8"
