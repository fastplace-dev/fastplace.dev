"""`fastplace shell` — interactive shell loaded with framework context."""

from __future__ import annotations

import asyncio
import code

import typer

from fastplace.console import console


def _bootstrap() -> dict:
    """Load env/config and expose the framework context as shell globals."""
    from fastplace.config import load_env, reset_config

    load_env()
    reset_config()

    context: dict = {}

    from fastplace.config import Config

    context["config"] = Config()

    try:
        from fastplace.db import db

        context["db"] = db
    except Exception:  # pragma: no cover - ORM optional at shell time
        pass

    try:
        from fastplace.orm import Model

        context["Model"] = Model
    except Exception:  # pragma: no cover
        pass

    context["asyncio"] = asyncio
    return context


def _load_models(context: dict) -> dict:
    """Import every model under app/modules/**/models for quick shell access."""
    try:
        from fastplace.orm.registry import import_all_models

        import_all_models()
    except Exception:
        return context

    from fastplace.orm import Model

    for subclass in Model.__subclasses__():
        context[subclass.__name__] = subclass
    return context


shell_app = typer.Typer(help="Interactive shell.")


@shell_app.command("shell")
def shell() -> None:
    """Launch an interactive shell loaded with models and framework context."""
    context = _load_models(_bootstrap())
    banner = "Fastplace shell — models, db, and config are available."
    console.print(f"[fastplace]{banner}[/]")
    try:
        from IPython import start_ipython  # type: ignore

        start_ipython(argv=[], user_ns=context)
    except ImportError:
        code.interact(banner=banner, local=context, exitmsg="")
