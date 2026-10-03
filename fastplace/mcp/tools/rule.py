"""``record-rule`` — durable project rules in ``.fastplace/rules/``."""

from __future__ import annotations

import re

from fastplace.mcp.context import McpContext
from fastplace.mcp.rules import RuleError, RuleRepository


def build_record_rule(ctx: McpContext):
    async def record_rule(glob: str, title: str, note: str) -> str:
        if not ctx.config.rules:
            raise ValueError("Rule recording is disabled. Set FASTPLACE_MCP_RULES=1 to enable it.")
        repo = RuleRepository(ctx.root / ".fastplace" / "rules", base_path=ctx.root)
        try:
            location = repo.write(glob, title, note)
        except RuleError as exc:
            raise ValueError(str(exc)) from exc
        clean_title = re.sub(r"\s+", " ", title).strip()
        return f"Recorded rule in {repo.relative_path(location)}: {clean_title}."

    return record_rule
