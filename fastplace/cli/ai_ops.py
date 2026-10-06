"""App-plane AI commands: chat, tools, agents, embeddings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import typer

from fastplace.console import console

if TYPE_CHECKING:  # annotations stay lazy; runtime imports remain function-local
    from fastplace.cli._doctor import Check

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
    from fastplace.cli.inspect import _import_project_registrations

    _import_project_registrations(lambda: import_tools(root))
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
    agent_name: str = typer.Option(
        "", "--agent", help="Project agent factory to use (see ai:agents)."
    ),
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
        from fastplace.cli.inspect import _import_project_registrations

        _import_project_registrations(lambda: import_agents(root))
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
        try:
            failed = asyncio.run(_stream_run())
        except Exception as exc:  # noqa: BLE001 — provider errors report, not traceback
            console.print(f"[red]{type(exc).__name__}:[/] {exc}")
            raise typer.Exit(code=1) from exc
        if failed:
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


@ai_ops_app.command("ai:tool:show")
def ai_tool_show(name: str) -> None:
    """Show one tool's provider wire schema and its validation model."""
    import json

    from fastplace.config import load_env

    load_env()
    from fastplace.cli.system import _project_root

    root = _project_root()
    from fastplace.ai import import_tools, tool_registry
    from fastplace.ai.tool import args_model
    from fastplace.cli.inspect import _import_project_registrations

    _import_project_registrations(lambda: import_tools(root))
    spec = tool_registry.get(name)
    if spec is None:
        console.print(f"[red]no tool named '{name}'[/]")
        raise typer.Exit(code=1)

    console.print_json(json.dumps(spec.to_openai()))
    model = args_model(spec)
    required = [
        field_name for field_name, field in model.model_fields.items() if field.is_required()
    ]
    from rich.table import Table

    table = Table(title=f"Validation model — {name}")
    for column in ("field", "type", "required"):
        table.add_column(column)
    for field_name, field in model.model_fields.items():
        annotation = getattr(field.annotation, "__name__", str(field.annotation))
        table.add_row(field_name, annotation, "yes" if field_name in required else "no")
    console.print(table)


@ai_ops_app.command("ai:agents")
def ai_agents() -> None:
    """List the project's agent factories with model and tool count."""
    from fastplace.config import load_env

    load_env()
    from fastplace.cli.system import _project_root

    root = _project_root()
    from fastplace.ai import import_agents, registered_agent_factories
    from fastplace.cli.inspect import _import_project_registrations

    _import_project_registrations(lambda: import_agents(root))
    factories = registered_agent_factories()
    if not factories:
        console.print("[dim]no agent factories in app/ai/agents/[/]")
        return

    from rich.table import Table

    table = Table(title="AI agents")
    for column in ("agent", "module", "model", "tools"):
        table.add_column(column)
    for factory in factories:
        try:
            agent_obj = factory.fn()  # config + tool resolution only — no LLM call
            table.add_row(
                factory.name, factory.module, str(agent_obj.model), str(len(agent_obj.tools))
            )
        except Exception as exc:  # noqa: BLE001 — one broken factory must not kill the table
            table.add_row(factory.name, factory.module, f"[red]error: {exc}[/]", "—")
    console.print(table)


def _vector_columns(model_cls: Any) -> list[tuple[str, int | None]]:
    """(column name, declared dims or None) for every vector-typed column.

    PortableVector (the VectorField column type) carries ``.dimensions``;
    a raw pgvector Vector carries ``.dim``; the legacy VectorJSON fallback
    declares nothing — that None is the caller's 'backend does not store
    dims' case.
    """
    from fastplace.orm.types import PortableVector, VectorJSON

    try:
        from pgvector.sqlalchemy import Vector
    except ImportError:  # pragma: no cover — pgvector is a project dependency
        Vector = None  # type: ignore[assignment,misc]

    found: list[tuple[str, int | None]] = []
    for column in model_cls.__table__.columns:
        if isinstance(column.type, PortableVector):
            found.append((column.name, column.type.dimensions))
        elif isinstance(column.type, VectorJSON) or (
            Vector is not None and isinstance(column.type, Vector)
        ):
            found.append((column.name, getattr(column.type, "dim", None)))
    return found


@ai_ops_app.command("ai:embed")
def ai_embed(
    text: str,
    model: str = typer.Option("", "--model", help="Embedding model override."),
    check: str = typer.Option("", "--check", help="Model class whose VectorField dims to verify."),
    n: int = typer.Option(4, "--n", help="How many floats to print."),
) -> None:
    """Embed TEXT and report model, dimension, and the first N floats."""
    import asyncio

    from fastplace.config import config, load_env

    load_env()
    resolved = model or str(config("AI_EMBEDDING_MODEL", default="text-embedding-3-small"))

    from fastplace.ai.embeddings import embed

    vector = asyncio.run(embed(text, model=model or None))
    floats = ", ".join(f"{value:.6f}" for value in vector[: max(n, 0)])
    console.print(f"model:     {resolved}")
    console.print(f"dimension: {len(vector)}")
    console.print(f"first {min(max(n, 0), len(vector))}: [{floats}]")

    if not check:
        return

    from fastplace.cli.inspect import collect_models
    from fastplace.cli.system import _project_root

    models = collect_models(_project_root())
    # Same-named classes accumulate when several projects are imported in one
    # process (the declarative registry never forgets); the class the CURRENT
    # sys.modules state resolves to is the one this project just defined.
    import sys

    model_cls = next(
        (
            cls
            for cls in models
            if cls.__name__ == check
            and getattr(sys.modules.get(cls.__module__), check, None) is cls
        ),
        None,
    )
    if model_cls is None:
        console.print(f"[red]no model class named '{check}'[/]")
        raise typer.Exit(code=1)

    columns = _vector_columns(model_cls)
    if not columns:
        console.print(f"[dim]{check} declares no vector fields[/]")
        return

    mismatched = False
    for name, dims in columns:
        if dims is None:
            console.print(
                f"[dim]{check}.{name}: dims not declared on this backend (sqlite/json vectors)[/]"
            )
        elif dims == len(vector):
            console.print(f"[green]match[/]   {check}.{name}: {dims}")
        else:
            mismatched = True
            console.print(
                f"[red]mismatch[/] {check}.{name}: declared {dims}, embedding returns {len(vector)}"
            )
    if mismatched:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# ai:doctor — roadmap app plane doctor
# ---------------------------------------------------------------------------

#: Known output dimensions for shipped embedding models. Anything outside
#: this table cannot be statically checked — the live check is `ai:embed
#: --check`, which embeds and measures.
_EMBEDDING_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}


def _check_provider_key() -> Check:
    import os

    from fastplace.cli._doctor import Check
    from fastplace.config import config

    model = str(config("AI_MODEL", default="")).lower()
    if "gpt" in model or "openai" in model:
        key_name = "OPENAI_API_KEY"
    elif "claude" in model or "anthropic" in model:
        key_name = "ANTHROPIC_API_KEY"
    else:
        present = [k for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY") if os.environ.get(k)]
        if present:
            key_name = present[0]
        else:
            return Check(
                "provider_key",
                "warn",
                "no provider key in environment (AI calls will fail)",
                "export OPENAI_API_KEY or ANTHROPIC_API_KEY",
            )
    key = os.environ.get(key_name, "")
    if not key:
        return Check("provider_key", "warn", f"{key_name} not set", f"export {key_name}")
    # Masked tail only — the body of a key never reaches the table.
    return Check("provider_key", "pass", f"{key_name} …{key[-4:]}")


def _check_models() -> Check:
    from fastplace.cli._doctor import Check
    from fastplace.config import config

    chat = str(config("AI_MODEL", default="gpt-4o-mini") or "gpt-4o-mini")
    embed = str(
        config("AI_EMBEDDING_MODEL", default="text-embedding-3-small") or "text-embedding-3-small"
    )
    return Check("models", "pass", f"chat={chat} embed={embed}")


def _check_vector_store(root) -> Check:  # noqa: ANN001 — Path, imported lazily
    from fastplace.ai.vectors import active_vector_store, import_vector_stores
    from fastplace.cli._doctor import Check
    from fastplace.errors import ConfigurationError

    import_vector_stores(root)
    try:
        store = active_vector_store()
    except ConfigurationError as exc:
        return Check(
            "vector_store",
            "fail",
            str(exc),
            "fix AI_VECTOR_STORE in .env or register the store in app/ai/vectors/",
        )
    return Check(
        "vector_store", "pass", type(store).__name__.removesuffix("Store").lower() or "pgvector"
    )


def _declared_vector_columns(root):  # noqa: ANN001 — Path, imported lazily
    from fastplace.cli.inspect import collect_models

    declared: list[tuple[str, int | None]] = []
    for model_cls in collect_models(root):
        for column, dims in _vector_columns(model_cls):
            declared.append((f"{model_cls.__name__}.{column}", dims))
    return declared


def _check_vector_capability(root) -> Check:  # noqa: ANN001 — Path, imported lazily
    from fastplace.cli._doctor import Check
    from fastplace.cli.db_ops import _db_url_family

    declared = _declared_vector_columns(root)
    if not declared:
        return Check("vector_capability", "pass", "no vector columns declared")
    family, url = _db_url_family()
    if family and family != "postgresql":
        return Check(
            "vector_capability",
            "fail",
            f"vector columns {', '.join(name for name, _ in declared)} need pgvector, but DATABASE_URL is {family}",
            "switch DATABASE_URL to postgresql:// and run CREATE EXTENSION vector",
        )
    return Check(
        "vector_capability", "pass", f"pgvector stack for {', '.join(name for name, _ in declared)}"
    )


def _check_dimensions(root) -> Check:  # noqa: ANN001 — Path, imported lazily
    from fastplace.cli._doctor import Check
    from fastplace.config import config

    declared = _declared_vector_columns(root)
    if not declared:
        return Check("dimensions", "pass", "no vector columns declared")
    model = str(
        config("AI_EMBEDDING_MODEL", default="text-embedding-3-small") or "text-embedding-3-small"
    )
    expected = _EMBEDDING_DIMS.get(model)
    if expected is None:
        return Check(
            "dimensions",
            "warn",
            f"unknown output dimensions for {model} — verify VectorField sizes",
            "check the provider docs, or run fastplace ai:embed --check",
        )
    mismatched = [
        f"{name}({dims})" for name, dims in declared if dims is not None and dims != expected
    ]
    if mismatched:
        return Check(
            "dimensions",
            "fail",
            f"{model} emits {expected} dims but declared: {', '.join(mismatched)}",
            f"declare VectorField(dimensions={expected}) or switch AI_EMBEDDING_MODEL",
        )
    return Check("dimensions", "pass", f"{len(declared)} column(s) at {expected} dims")


@ai_ops_app.command("ai:doctor")
def ai_doctor() -> None:
    """AI stack diagnosis: keys, models, vector store, DB capability, dimensions."""
    from fastplace.cli._doctor import run_checks
    from fastplace.cli.system import _project_root
    from fastplace.config import load_env, reset_config

    root = _project_root()
    load_env(root / ".env")
    # Bind the config registry to the invoked project (doctor umbrella pattern).
    reset_config(root)

    # Named closures (not lambdas) so a raising check degrades to a readable
    # row name — run_checks derives it from __name__.
    def _vector_store_check() -> Check:
        return _check_vector_store(root)

    def _vector_capability_check() -> Check:
        return _check_vector_capability(root)

    def _dimensions_check() -> Check:
        return _check_dimensions(root)

    code = run_checks(
        "AI doctor",
        [
            _check_provider_key,
            _check_models,
            _vector_store_check,
            _vector_capability_check,
            _dimensions_check,
        ],
    )
    raise typer.Exit(code=code)
