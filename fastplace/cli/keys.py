"""Key management commands — APP_KEY generation (spec #32)."""

from __future__ import annotations

import re
import secrets
import shutil
from pathlib import Path

import typer

from fastplace.cli.database import _ensure_example_keys, _upsert_env_lines
from fastplace.console import console

keys_app = typer.Typer(help="Application key management.")


def _project_root() -> Path:
    """The cwd when it is a Fastplace project; a friendly exit otherwise."""
    root = Path.cwd()
    if not (root / "asgi.py").is_file():
        console.print(
            "[red]not inside a Fastplace project[/] — run this from a project root "
            "(the directory containing asgi.py)."
        )
        raise typer.Exit(code=1)
    return root


def _active_app_key(env_text: str) -> str | None:
    """The non-empty, uncommented APP_KEY value in .env text, if any.

    ``fastplace new`` ships a generated key, so an active value means one was
    deliberately chosen — silently replacing it would invalidate every signed
    session, CSRF token, and JWT the app ever issued.
    """
    for line in env_text.splitlines():
        match = re.match(r"^\s*APP_KEY\s*=\s*(\S+)\s*$", line)
        if match is not None:
            return match.group(1)
    return None


@keys_app.command("key:generate")
def key_generate(
    show: bool = typer.Option(
        False, "--show", help="Print a fresh key to stdout without writing .env."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Replace an already-set APP_KEY (the previous .env is kept as .env.bak).",
    ),
) -> None:
    """Generate an APP_KEY secret into .env (--show prints one without writing)."""
    root = _project_root()
    key = secrets.token_urlsafe(48)  # 64 url-safe chars — the `fastplace new` format
    if show:
        # Raw value only, markup off — pipeable straight into a .env edit.
        console.print(key, markup=False, highlight=False)
        return

    env = root / ".env"
    if not env.exists():
        example = root / ".env.example"
        env.write_text(example.read_text() if example.exists() else "")
    else:
        current = _active_app_key(env.read_text())
        if current is not None and not force:
            console.print("[red]refusing to overwrite[/] APP_KEY — already set in .env")
            console.print(
                "re-run with [cyan]--force[/] to rotate it "
                "(the previous .env is kept as .env.bak)"
            )
            raise typer.Exit(code=1)
        if current is not None and env.read_text().strip():
            shutil.copyfile(env, env.parent / (env.name + ".bak"))
    env.write_text(_upsert_env_lines(env.read_text(), {"APP_KEY": key}))
    # .env.example documents the key — a commented placeholder, never the secret.
    _ensure_example_keys(root / ".env.example", {"APP_KEY": ""})
    console.print("[green]set[/] APP_KEY in .env")
