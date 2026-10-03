"""MCP server assembly — ``fastplace-aibrain``.

Builds an :class:`MCPServer`, registers the tool set behind their gates
(per-tool env switches plus ``FASTPLACE_MCP_TOOLS_EXCLUDE``), and exposes the
application-info resource and the code-simplifier prompt. Tools are built as
closures over an :class:`~fastplace.mcp.context.McpContext`; database tools
share one engine resolver so tests can inject engines.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.prompts import Prompt
from mcp.server.mcpserver.resources import FunctionResource
from mcp.types import ToolAnnotations

from fastplace.mcp.context import McpContext
from fastplace.mcp.tools.database import (
    build_database_connections,
    build_database_query,
    build_database_schema,
)
from fastplace.mcp.tools.docs import build_search_docs
from fastplace.mcp.tools.info import build_application_info, build_get_absolute_url
from fastplace.mcp.tools.logs_tool import (
    build_browser_logs,
    build_last_error,
    build_read_log_entries,
)
from fastplace.mcp.tools.rule import build_record_rule
from fastplace.mcp.tools.tinker import build_tinker

SERVER_NAME = "fastplace-aibrain"


def _framework_version() -> str:
    """The running fastplace distribution's version — clients render it as
    ``serverInfo.version`` on connect (the SDK default is an empty string)."""
    from importlib.metadata import version as dist_version

    try:
        return dist_version("fastplace")
    except Exception:  # noqa: BLE001 — a source checkout without an installed dist
        return ""


INSTRUCTIONS = (
    "Tools for understanding and operating a Fastplace application: read the "
    "database (read-only), tail application and browser logs, inspect the "
    "installed packages, search the framework docs, and record durable "
    "project rules. Call application-info on every new chat to learn the "
    "framework, Python, and package versions before writing code."
)

_CODE_SIMPLIFIER_PROMPT = """\
Evaluate the target code for clarity, simplicity, and consistency with the
project's conventions, then propose a cleaner version. Apply the framework's
own style: Controllers stay thin and delegate to services; repositories own
data access; models stay declarative. Preserve behavior exactly — this is a
refactor pass, not a rewrite. Point out anything that fights the framework's
conventions and suggest the idiomatic replacement.

Target:

{code}
"""

Readonly = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def build_server(
    ctx: McpContext,
    *,
    manager: object | None = None,
    engine_resolver: Callable | None = None,
) -> MCPServer:
    """Assemble the ``fastplace-aibrain`` server for a project root."""
    server = MCPServer(name=SERVER_NAME, version=_framework_version(), instructions=INSTRUCTIONS)
    exclude = ctx.config.tools_exclude

    def register(name: str, fn: Callable, description: str, *, readonly: bool = True) -> None:
        if name in exclude:
            return
        server.add_tool(
            fn,
            name=name,
            description=description,
            annotations=Readonly if readonly else None,
        )

    register(
        "application-info",
        build_application_info(ctx),
        "Get comprehensive application information: Fastplace version, Python "
        "version, database engine, configured app URL, and every installed "
        "Python/JS package with its version. Use this on each new chat and "
        "write version-specific code against the packages it reports.",
    )
    register(
        "database-connections",
        build_database_connections(ctx, manager=manager),
        "List the configured database connection names and the default one.",
    )
    register(
        "database-schema",
        build_database_schema(ctx, engine_resolver=engine_resolver),
        "Inspect the database schema: every table (with driver and database "
        "name), or the columns, types, and indexes of one table via the "
        "'table' argument.",
    )
    register(
        "database-query",
        build_database_query(ctx, engine_resolver=engine_resolver),
        "Execute a read-only SQL query (SELECT, SHOW, EXPLAIN, DESCRIBE) "
        "against the application database. Writes are rejected twice — "
        "lexically and by an engine-enforced read-only transaction. Use the "
        "'database' argument to target a named connection.",
    )
    register(
        "read-log-entries",
        build_read_log_entries(ctx),
        "Read the last N entries from the application log, correctly joining "
        "multi-line stack traces into single entries.",
    )
    register(
        "last-error",
        build_last_error(ctx),
        "Return the most recent ERROR (or higher) log entry including its "
        "stack trace, if one exists in the recent log window.",
    )
    register(
        "get-absolute-url",
        build_get_absolute_url(ctx),
        "Convert a relative path (e.g. '/dashboard') into an absolute URL "
        "using the application's configured APP_URL.",
    )
    register(
        "search-docs",
        build_search_docs(ctx),
        "Search the Fastplace documentation. Pass multiple queries when "
        "unsure of the exact terminology. Returns a token-budgeted markdown "
        "digest; raise token_limit (e.g. 5000) when results are truncated.",
    )
    if ctx.config.browser_logs:
        register(
            "browser-logs",
            build_browser_logs(ctx),
            "Read the last N browser console/error entries captured by the "
            "in-app log sink (console messages, uncaught exceptions, "
            "unhandled rejections).",
        )
    if ctx.config.rules:
        register(
            "record-rule",
            build_record_rule(ctx),
            "Record a durable project rule in .fastplace/rules/, grouped by "
            "area. Only call when the user explicitly asks to record, "
            "remember, or document a rule — work instructions are not rules. "
            "Never call on your own initiative.",
            readonly=False,
        )
    if ctx.config.tinker:
        register(
            "tinker",
            build_tinker(ctx),
            "Execute Python code against the booted application in an "
            "isolated subprocess (model queries, factories, quick scripts). "
            "Requires FASTPLACE_MCP_TINKER=1.",
            readonly=False,
        )

    server.add_resource(
        FunctionResource(
            uri="fastplace://application-info",
            name="application-info",
            description="The same payload as the application-info tool, as a resource.",
            mime_type="application/json",
            fn=lambda: json.dumps({"hint": "Use the application-info tool instead."}),
        )
    )

    def code_simplifier(code: str) -> str:
        """The code to simplify."""
        return _CODE_SIMPLIFIER_PROMPT.format(code=code)

    server.add_prompt(
        Prompt.from_function(
            code_simplifier,
            name="fastplace-code-simplifier",
            description=(
                "Review and simplify a piece of Fastplace code in line with "
                "framework conventions (thin controllers, service layer, "
                "declarative models)."
            ),
        )
    )

    return server
