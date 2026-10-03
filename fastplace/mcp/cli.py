"""``fastplace mcp`` — run and install the ``fastplace-aibrain`` MCP server."""

from __future__ import annotations

from pathlib import Path

import typer

mcp_app = typer.Typer(help="MCP server (fastplace-aibrain) operations.")


def _project_root() -> Path:
    return Path.cwd()


def _build_context():
    """Load .env + config and return the MCP context for this project."""
    from fastplace.config import load_env
    from fastplace.mcp.config import McpConfig
    from fastplace.mcp.context import McpContext

    load_env()
    return McpContext(root=_project_root(), config=McpConfig.from_env())


@mcp_app.command("start")
def start(
    check: bool = typer.Option(
        False, "--check", help="Validate configuration and list registered tools, then exit."
    ),
) -> None:
    """Run the MCP server over stdio (this is what agent configs launch)."""
    from fastplace.console import console

    ctx = _build_context()
    if not ctx.config.enabled:
        console.print("[red]MCP server is disabled[/] — set FASTPLACE_MCP_ENABLED=1 to run it.")
        raise typer.Exit(1)

    from fastplace.mcp.server import build_server

    server = build_server(ctx)

    if check:
        import asyncio

        tools = asyncio.run(server.list_tools())
        console.print(f"[bold]{server.name}[/] — {len(tools)} tools registered:")
        for tool in sorted(tools, key=lambda t: t.name):
            console.print(f"  • {tool.name}")
        return

    import anyio

    from fastplace.console import err_console

    # The banner must never touch stdout — stdio MCP streams are JSON-RPC only.
    err_console.print(f"[bold]{server.name}[/] listening on stdio…")
    anyio.run(server.run_stdio_async)


@mcp_app.command("install")
def install(
    agents: list[str] = typer.Argument(None, help="Agent keys (default: auto-detect)."),
    force: bool = typer.Option(False, "--force", help="Rewrite even when nothing changed."),
) -> None:
    """Write agent MCP configuration + AGENTS.md guidelines + skills."""
    from fastplace.console import console

    ctx = _build_context()
    if not ctx.config.enabled:
        console.print("[red]MCP server is disabled[/] — set FASTPLACE_MCP_ENABLED=1.")
        raise typer.Exit(1)

    from rich.table import Table

    from fastplace.mcp.install import run_install

    try:
        report = run_install(ctx, agents=list(agents) if agents else None, force=force)
    except KeyError as exc:
        console.print(
            f"[red]Unknown agent[/] {exc} — pick from: claude_code, cursor, "
            "codex, copilot, zed, amp, antigravity, opencode, factory, "
            "grok_build, junie, kiro, pi"
        )
        raise typer.Exit(1) from exc

    table = Table(title="fastplace-aibrain install", show_header=True, expand=False)
    table.add_column("Agent")
    table.add_column("Surface")
    table.add_column("Status")
    for row in report.rows:
        table.add_row(row.agent, row.surface, row.status)
    console.print(table)
    console.print("Restart your agent so it picks up the new MCP server.")


@mcp_app.command("update")
def update() -> None:
    """Re-sync guidelines, skills, and MCP configuration after an upgrade."""
    from fastplace.console import console

    ctx = _build_context()
    if not ctx.config.enabled:
        console.print("[red]MCP server is disabled[/] — set FASTPLACE_MCP_ENABLED=1.")
        raise typer.Exit(1)

    from fastplace.mcp.install import run_update

    try:
        report = run_update(ctx)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc

    from rich.table import Table

    table = Table(title="fastplace-aibrain update", show_header=True, expand=False)
    table.add_column("Agent")
    table.add_column("Surface")
    table.add_column("Status")
    for row in report.rows:
        table.add_row(row.agent, row.surface, row.status)
    console.print(table)
