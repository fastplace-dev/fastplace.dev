"""search-docs — thin proxy to the hosted docs MCP-digest API.

httpx.MockTransport stands in for the network so the request shape and error
paths are verified without a live server.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from fastplace.mcp.config import McpConfig
from fastplace.mcp.context import McpContext
from fastplace.mcp.tools.docs import build_search_docs


@pytest.fixture()
def ctx(tmp_path: Path) -> McpContext:
    return McpContext(root=tmp_path, config=McpConfig(api_url="https://docs.test"))


class RecordingTransport(httpx.AsyncBaseTransport):
    """Scripted responses + captured request bodies."""

    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture()
def transport() -> RecordingTransport:
    return RecordingTransport(
        httpx.Response(
            200,
            json={
                "digest": "### Routing\n/docs/0.3/routing — basics\n\nFound content.\n\n",
                "count": 1,
                "truncated": False,
                "version": "0.3",
            },
        )
    )


def _handle(ctx: McpContext, transport: RecordingTransport):
    return build_search_docs(ctx, transport=transport)


async def test_search_docs_posts_queries_and_returns_digest(ctx, transport):
    out = await _handle(ctx, transport)(queries=["queue dispatch", "job retry"])

    assert out == "### Routing\n/docs/0.3/routing — basics\n\nFound content.\n\n"
    body = json.loads(transport.requests[0].content)
    assert body["queries"] == ["queue dispatch", "job retry"]
    assert body["token_limit"] == 3000
    assert set(body) == {"queries", "token_limit"}
    assert transport.requests[0].url == "https://docs.test/api/v1/docs/mcp"


async def test_search_docs_forwards_token_limit(ctx, transport):
    await _handle(ctx, transport)(queries=["search"], token_limit=8000)

    body = json.loads(transport.requests[0].content)
    assert body["token_limit"] == 8000


async def test_search_docs_falls_back_to_raw_text_without_json(ctx):
    transport = RecordingTransport(httpx.Response(200, text="plain markdown"))
    out = await _handle(ctx, transport)(queries=["x"])

    assert out == "plain markdown"


async def test_search_docs_rejects_empty_queries(ctx, transport):
    with pytest.raises(ValueError, match="quer"):
        await _handle(ctx, transport)(queries=[])


async def test_search_docs_http_error_raises(ctx):
    transport = RecordingTransport(httpx.Response(502, text="bad gateway"))
    with pytest.raises(ValueError, match="502"):
        await _handle(ctx, transport)(queries=["x"])


async def test_search_docs_network_error_raises(ctx):
    transport = RecordingTransport(httpx.ConnectError("connection refused"))
    with pytest.raises(ValueError, match="unreachable"):
        await _handle(ctx, transport)(queries=["x"])
