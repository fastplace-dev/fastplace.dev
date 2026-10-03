"""record-rule tool wrapper — gating + formatting."""

from __future__ import annotations

from pathlib import Path

import pytest

from fastplace.mcp.config import McpConfig
from fastplace.mcp.context import McpContext
from fastplace.mcp.tools.rule import build_record_rule


@pytest.fixture()
def ctx(tmp_path: Path) -> McpContext:
    return McpContext(root=tmp_path, config=McpConfig(rules=True))


async def test_records_into_project_rules_dir(ctx, tmp_path: Path):
    handle = build_record_rule(ctx)
    out = await handle(
        glob="app/http/controllers/**",
        title="Extend BaseController",
        note="Use it for tenant scoping.",
    )

    assert "Recorded rule in" in out
    assert (tmp_path / ".fastplace" / "rules" / "index.md").exists()


async def test_disabled_gate(tmp_path: Path):
    ctx = McpContext(root=tmp_path, config=McpConfig(rules=False))
    handle = build_record_rule(ctx)

    with pytest.raises(ValueError, match="disabled"):
        await handle(glob="app/**", title="t", note="n")


async def test_empty_fields_reported(ctx):
    handle = build_record_rule(ctx)

    with pytest.raises(ValueError, match="non-empty"):
        await handle(glob="app/**", title="", note="note")
