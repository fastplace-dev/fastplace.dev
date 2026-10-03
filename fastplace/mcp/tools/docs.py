"""``search-docs`` — thin client over the hosted docs MCP-digest API.

All ranking, merging, and token budgeting live server-side (the docs site
indexes its own pages); this tool only forwards the query set and hands
back the markdown digest.
"""

from __future__ import annotations

from typing import Any

import httpx

from fastplace.mcp.context import McpContext


def build_search_docs(ctx: McpContext, *, transport: httpx.AsyncBaseTransport | None = None):
    async def search_docs(
        queries: list[str],
        token_limit: int = 3000,
    ) -> str:
        if not queries or not any(q.strip() for q in queries):
            raise ValueError(
                "Provide at least one search query. Pass multiple queries when "
                "unsure of the exact terminology (e.g. 'dispatch job' and 'queue')."
            )

        payload: dict[str, Any] = {
            "queries": [q for q in queries if q.strip()],
            "token_limit": token_limit,
        }
        try:
            async with httpx.AsyncClient(
                timeout=float(ctx.config.tool_timeout), transport=transport
            ) as client:
                response = await client.post(
                    f"{ctx.config.api_url.rstrip('/')}/api/v1/docs/mcp", json=payload
                )
        except httpx.HTTPError as exc:
            raise ValueError(f"Docs API unreachable ({exc.__class__.__name__}: {exc}).") from exc

        if response.status_code != 200:
            raise ValueError(
                f"Docs API returned HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            return response.json().get("digest", response.text)
        except ValueError:
            return response.text

    return search_docs
