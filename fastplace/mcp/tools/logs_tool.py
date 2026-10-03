"""Log tools: ``read-log-entries``, ``last-error``, ``browser-logs``."""

from __future__ import annotations

from pathlib import Path

from fastplace.config import Config
from fastplace.mcp.context import McpContext
from fastplace.mcp.logs import read_last_entries, read_last_error_entry, resolve_log_file


def _resolve_log_path(ctx: McpContext) -> Path:
    config = Config(ctx.root)
    channel = config.get("LOG_CHANNEL")
    return resolve_log_file(ctx.root, channel=channel)


def build_read_log_entries(ctx: McpContext):
    async def read_log_entries(entries: int) -> str:
        if entries <= 0:
            return 'The "entries" argument must be greater than 0.'

        log_file = _resolve_log_path(ctx)
        if not log_file.exists():
            return f"Log file not found at {log_file}"

        found = read_last_entries(log_file, entries)
        if not found:
            return "No log entries yet."
        return "\n\n".join(entry.strip() for entry in found)

    return read_log_entries


def build_last_error(ctx: McpContext):
    async def last_error() -> str:
        log_file = _resolve_log_path(ctx)
        if not log_file.exists():
            return f"Log file not found at {log_file}"

        entry = read_last_error_entry(log_file)
        if entry is None:
            return "No error entries found in the log."
        return entry

    return last_error


def _format_browser_entry(entry: dict) -> str:
    prefix = str(entry.get("level", "log")).upper()
    url = entry.get("url") or ""
    location = f" @ {url}" if url else ""
    stack = entry.get("stack") or ""
    body = f"{entry.get('message', '')}\n{stack}".strip()
    return f"[BROWSER {prefix}]{location} {body}"


def build_browser_logs(ctx: McpContext):
    async def browser_logs(entries: int) -> str:
        if entries <= 0:
            return 'The "entries" argument must be greater than 0.'

        from fastplace.mcp.browser import get_buffer

        found = get_buffer().tail(entries)
        if not found:
            return "No browser logs captured yet."
        return "\n\n".join(_format_browser_entry(entry) for entry in found)

    return browser_logs
