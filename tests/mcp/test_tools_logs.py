"""Log tools: read-log-entries, last-error, browser-logs."""

from __future__ import annotations

from pathlib import Path

import pytest

from fastplace.mcp.browser import BrowserLogBuffer
from fastplace.mcp.config import McpConfig
from fastplace.mcp.context import McpContext
from fastplace.mcp.tools.logs_tool import (
    build_browser_logs,
    build_last_error,
    build_read_log_entries,
)


@pytest.fixture()
def ctx(tmp_path: Path) -> McpContext:
    log_dir = tmp_path / "storage" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "fastplace.log").write_text(
        "2026-10-03 10:00:00 INFO app [request_id= job_id=] boot\n"
        "2026-10-03 10:00:10 ERROR http [request_id=r2 job_id=] boom\n"
        "Traceback (most recent call last):\n"
        '  File "app.py", line 1\n'
        "ValueError: boom\n"
        "2026-10-03 10:00:20 INFO http [request_id=r3 job_id=] done\n"
    )
    return McpContext(root=tmp_path, config=McpConfig())


async def test_read_log_entries(ctx):
    handle = build_read_log_entries(ctx)
    out = await handle(entries=2)

    assert "10:00:10 ERROR" in out
    assert "10:00:20 INFO" in out
    assert "boot" not in out


async def test_read_log_entries_rejects_non_positive(ctx):
    handle = build_read_log_entries(ctx)
    assert "greater than 0" in await handle(entries=0)


async def test_read_log_entries_missing_file(tmp_path):
    ctx = McpContext(root=tmp_path, config=McpConfig())
    handle = build_read_log_entries(ctx)

    assert "not found" in (await handle(entries=5)).lower()


async def test_last_error_returns_entry_with_trace(ctx):
    handle = build_last_error(ctx)
    out = await handle()

    assert out.startswith("2026-10-03 10:00:10 ERROR")
    assert "ValueError: boom" in out


async def test_last_error_none_is_soft(ctx, tmp_path):
    log_dir = tmp_path / "storage" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "fastplace.log").write_text(
        "2026-10-03 10:00:00 INFO app [request_id= job_id=] fine\n"
    )
    handle = build_last_error(ctx)

    assert "no error" in (await handle()).lower()


def test_browser_ring_buffer_cap_and_tail():
    buffer = BrowserLogBuffer(cap=3)
    for i in range(5):
        buffer.add({"level": "error", "message": f"m{i}"})

    tail = buffer.tail(2)

    assert [e["message"] for e in tail] == ["m3", "m4"]
    assert len(buffer.tail(10)) == 3


async def test_browser_logs_tool_reads_buffer(ctx):
    from fastplace.mcp.browser import get_buffer

    get_buffer().add({"level": "error", "message": "TypeError: x is undefined"})

    handle = build_browser_logs(ctx)
    out = await handle(entries=10)

    assert "TypeError: x is undefined" in out


async def test_browser_logs_empty_is_soft(ctx):
    from fastplace.mcp.browser import get_buffer

    get_buffer().clear()
    handle = build_browser_logs(ctx)

    assert "no browser logs" in (await handle(entries=10)).lower()
