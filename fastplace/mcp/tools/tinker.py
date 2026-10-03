"""``tinker`` — gated, subprocess-isolated Python evaluation.

Every call spawns a fresh interpreter (``-m fastplace.mcp._tinker_child``)
rooted at the project directory, streams the code over stdin, and enforces
the tool timeout from the parent side. A crash, hang, or ``os._exit`` in the
user code can never take down the MCP server. Off unless
``FASTPLACE_MCP_TINKER`` is enabled.
"""

from __future__ import annotations

import asyncio
import json
import sys

from fastplace.mcp.context import McpContext

_MAX_OUTPUT = 50_000


def build_tinker(ctx: McpContext):
    async def tinker(code: str, timeout: float | None = None) -> str:
        if not ctx.config.tinker:
            raise ValueError(
                "Tinker is disabled. Set FASTPLACE_MCP_TINKER=1 in the project "
                "environment to enable arbitrary code execution."
            )
        if not code.strip():
            raise ValueError("Provide Python code to execute.")

        budget = timeout if timeout is not None else float(ctx.config.tool_timeout)
        try:
            payload = await _run_child(ctx, code, budget)
        except TimeoutError:
            raise ValueError(
                f"Tinker execution timed out after {budget:g}s and the child process was killed."
            ) from None

        if not payload.get("ok"):
            error = payload.get("error", "unknown error")
            lines = [f"Tinker failed: {error}"]
            if payload.get("traceback"):
                lines.append(str(payload["traceback"]))
            return "\n".join(lines)

        result = payload.get("repr")
        return (
            "Result: " + str(result)
            if result is not None
            else "Executed successfully (no result value)."
        )

    return tinker


async def _run_child(ctx: McpContext, code: str, timeout: float) -> dict:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "fastplace.mcp._tinker_child",
        cwd=str(ctx.root),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(code.encode("utf-8")), timeout=timeout
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    except BaseException:
        process.kill()
        await process.wait()
        raise

    if process.returncode != 0:
        raise ValueError(f"Tinker child exited with status {process.returncode}.")

    try:
        payload = json.loads(stdout.decode("utf-8", errors="replace").strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Tinker child produced unreadable output: {exc}") from exc

    if isinstance(payload.get("repr"), str) and len(payload["repr"]) > _MAX_OUTPUT:
        payload["repr"] = payload["repr"][:_MAX_OUTPUT] + "… [truncated]"
    if isinstance(payload.get("traceback"), str) and len(payload["traceback"]) > _MAX_OUTPUT:
        payload["traceback"] = payload["traceback"][:_MAX_OUTPUT] + "… [truncated]"
    return payload
