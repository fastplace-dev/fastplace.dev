"""tinker — subprocess-isolated Python evaluation, gated off by default."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastplace.mcp._tinker_child import evaluate
from fastplace.mcp.config import McpConfig
from fastplace.mcp.context import McpContext


@pytest.fixture()
def ctx_enabled(tmp_path: Path) -> McpContext:
    return McpContext(root=tmp_path, config=McpConfig(tinker=True))


async def test_disabled_by_default(tmp_path: Path):
    ctx = McpContext(root=tmp_path, config=McpConfig(tinker=False))
    handle = build_handle(ctx)

    with pytest.raises(ValueError, match="disabled"):
        await handle(code="1 + 1")


def build_handle(ctx: McpContext):
    from fastplace.mcp.tools.tinker import build_tinker

    return build_tinker(ctx)


async def test_trailing_expression_captured(ctx_enabled):
    assert json.loads(evaluate("1 + 1")) == {"ok": True, "repr": "2"}


async def test_statements_run_and_side_effects_persist(ctx_enabled, tmp_path: Path):
    code = f"from pathlib import Path\nPath({str(tmp_path)!r}, 'marker.txt').write_text('hi')\n"
    result = json.loads(evaluate(code))

    assert result["ok"] is True
    assert (tmp_path / "marker.txt").read_text() == "hi"


async def test_runtime_error_reports_traceback(ctx_enabled):
    result = json.loads(evaluate("raise RuntimeError('boom')"))

    assert result["ok"] is False
    assert "boom" in result["error"]
    assert "Traceback" in result["traceback"]


async def test_syntax_error_is_soft(ctx_enabled):
    result = json.loads(evaluate("def broken(:"))

    assert result["ok"] is False
    assert "SyntaxError" in result["error"]


async def test_syntax_error_result_has_no_repr_key(ctx_enabled):
    result = json.loads(evaluate("def broken(:"))

    assert "repr" not in result


async def test_tool_timeout_kills_child(ctx_enabled):
    handle = build_handle(ctx_enabled)

    with pytest.raises(ValueError, match="timed out"):
        await handle(code="import time; time.sleep(30)", timeout=1.0)


async def test_tool_reports_error_text(ctx_enabled):
    handle = build_handle(ctx_enabled)

    out = await handle(code="raise RuntimeError('kaboom')")

    assert "kaboom" in out
    assert "Traceback" in out
