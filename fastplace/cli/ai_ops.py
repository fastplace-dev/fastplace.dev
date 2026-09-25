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


@ai_ops_app.command("ai:chat")
def ai_chat(
    message: str,
    agent_name: str = typer.Option("", "--agent", help="Project agent factory to use (see ai:agents)."),
    model: str = typer.Option("", "--model", help="Override the agent's model."),
    stream: bool = typer.Option(False, "--stream", help="Print the SSE event stream live."),
) -> None:
    """Send one message through an Agent and print the reply."""
    import asyncio

    from fastplace.config import load_env

    load_env()
    from fastplace.ai import Agent

    agent_obj: Agent
    if agent_name:
        from fastplace.cli.system import _project_root

        root = _project_root()
        from fastplace.ai import import_agents, registered_agent_factories

        import_agents(root)
        factories = {factory.name: factory for factory in registered_agent_factories()}
        factory = factories.get(agent_name)
        if factory is None:
            known = ", ".join(sorted(factories)) or "none found"
            console.print(f"[red]no agent named '{agent_name}'[/] — known: {known}")
            raise typer.Exit(code=1)
        agent_obj = factory.fn()
    else:
        agent_obj = Agent()
    if model:
        agent_obj.model = model

    async def _stream_run() -> bool:
        from fastplace.ai.stream import parse_sse, stream_events

        failed = False
        async for frame in stream_events(agent_obj, message):
            for event, data in parse_sse(frame):
                if event == "delta":
                    console.print(data.get("token", ""), end="", markup=False, highlight=False)
                elif event == "tool":
                    console.print(f"\n[blue]tool[/] {data.get('name', '')}")
                elif event == "done":
                    console.print("\n[green]done[/]")
                elif event == "error":
                    console.print(f"\n[red]error[/] {data.get('message', '')}")
                    failed = True
        return failed

    if stream:
        if asyncio.run(_stream_run()):
            raise typer.Exit(code=1)
        return

    # Unannotated on purpose: run() returns ``str | BaseModel`` and the CLI
    # prints either — mypy infers the union, strict mode is off.
    async def _run():
        return await agent_obj.run(message)

    try:
        reply = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — provider errors report, not traceback
        console.print(f"[red]{type(exc).__name__}:[/] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(reply)
