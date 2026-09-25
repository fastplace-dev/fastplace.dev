"""App-plane AI commands: chat, tools, agents, embeddings."""

from __future__ import annotations

import typer

from fastplace.console import console

ai_ops_app = typer.Typer(help="AI operations (chat, tools, agents, embeddings).")


@ai_ops_app.command("ai:tool:run")
def ai_tool_run(
    name: str,
    args: str = typer.Option("{}", "--args", help="Tool arguments as a JSON object."),
) -> None:
    """Run one registered project tool with validated JSON arguments."""
    import asyncio
    import inspect
    import json

    from fastplace.config import load_env

    load_env()
    from fastplace.cli.system import _project_root

    root = _project_root()
    from fastplace.ai import import_tools, tool_registry

    import_tools(root)
    spec = tool_registry.get(name)
    if spec is None:
        console.print(f"[red]no tool named '{name}'[/]")
        raise typer.Exit(code=1)
    try:
        payload = json.loads(args)
    except json.JSONDecodeError as exc:
        console.print(f"[red]invalid --args JSON:[/] {exc}")
        raise typer.Exit(code=1) from exc
    if not isinstance(payload, dict):
        console.print("[red]--args must be a JSON object[/]")
        raise typer.Exit(code=1)

    from pydantic import ValidationError

    from fastplace.ai.tool import args_model

    try:
        validated = args_model(spec).model_validate(payload)
    except ValidationError as exc:
        for error in exc.errors():
            field = ".".join(str(part) for part in error["loc"])
            console.print(f"[red]invalid argument:[/] {field} — {error['msg']}")
        raise typer.Exit(code=1) from exc

    async def _call():
        called = spec.fn(**{key: getattr(validated, key) for key in payload})
        if inspect.isawaitable(called):
            called = await called
        return called

    result = asyncio.run(_call())
    try:
        console.print_json(json.dumps(result, default=str))
    except (TypeError, ValueError):
        console.print(repr(result))
