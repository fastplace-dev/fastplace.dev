"""Provisioning commands — mail probes, personal access tokens, users.

``mail:test``, ``token:create``, ``token:revoke``, ``user:create``
(spec #44–#47) are operator-side tools: token:revoke acts immediately
without a confirmation prompt (it is not in the spec's destructive set),
a created token's plaintext is printed exactly once and never logged,
and passwords are only ever accepted — prompted hidden or passed as a
flag — never echoed or written.
"""

from __future__ import annotations

import asyncio
from typing import Any

import typer

from fastplace.config import config, load_env
from fastplace.console import console

provisioning_app = typer.Typer(help="Mail, token, and user provisioning.")


@provisioning_app.command("mail:test")
def mail_test(
    address: str = typer.Argument(..., help="Recipient address the probe is sent to."),
) -> None:
    """Send a probe email through the active mail transport."""
    load_env()
    from fastplace.mail import Mail, MailMessage

    driver = str(config("MAIL_DRIVER", default="log") or "log")

    async def _send() -> None:
        # The facade's public send: local drivers deliver inline, so the
        # probe reports transport failures immediately; smtp over saq
        # queues — the exact path real outbound mail takes.
        message = MailMessage(
            subject="Fastplace mail test",
            text=(
                "This is a probe from `fastplace mail:test`. "
                "If it arrived, the active mail transport is wired correctly."
            ),
            to=address,
        )
        await Mail.to(address).send(message)

    try:
        asyncio.run(_send())
    except Exception as exc:  # noqa: BLE001 — a probe reports the failure, never crashes raw
        console.print(f"[red]✗[/] mail test failed via the '{driver}' transport: {exc}")
        raise typer.Exit(code=1) from None

    queued = (
        driver == "smtp"
        and str(config("QUEUE_DRIVER", default="memory") or "memory") == "saq"
    )
    if queued:
        console.print(
            f"[green]✓[/] test mail to {address} queued for delivery "
            "(smtp rides the saq queue — run [bold]fastplace queue:work[/])"
        )
    else:
        console.print(f"[green]✓[/] test mail sent to {address} via the '{driver}' transport")


@provisioning_app.command("token:create")
def token_create(
    user: int = typer.Argument(..., help="User id the token belongs to."),
    name: str = typer.Argument("cli", help="Label identifying this token."),
) -> None:
    """Issue a personal access token; the plaintext is shown exactly once."""
    load_env()
    from fastplace.auth.tokens import create_token

    token = asyncio.run(create_token(user, name))
    console.print(f"[green]✓[/] personal access token '{name}' issued for user {user}")
    console.print(f"  [bold]{token}[/]")
    console.print("  [dim]store it now — it is hashed at rest and cannot be shown again[/]")


@provisioning_app.command("token:revoke")
def token_revoke(
    token_id: str = typer.Argument(
        None, help="Row id of one token to revoke (its owner is resolved automatically)."
    ),
    user: int | None = typer.Option(
        None, "--user", help="Revoke every token belonging to this user id."
    ),
    revoke_every: bool = typer.Option(
        False, "--all", help="Revoke every token on this installation."
    ),
) -> None:
    """Revoke personal access tokens — immediate, no confirmation (spec #46)."""
    load_env()
    from fastplace.auth.tokens import pat_store

    if token_id is not None and revoke_every:
        console.print("[red]--all cannot be combined with a token id[/]")
        raise typer.Exit(code=1)
    if token_id is None and user is None and not revoke_every:
        console.print("[red]nothing to revoke[/] — pass a token id, --user ID, or --all")
        raise typer.Exit(code=1)

    store = pat_store()

    if token_id is not None:
        try:
            parsed = int(token_id)
        except ValueError:
            console.print(f"[red]'{token_id}' is not a token id (a number)[/]")
            raise typer.Exit(code=1) from None
        # Owner-scoped delete keeps the store IDOR-safe; the console operator
        # passes --user or the owner is resolved from the row itself.
        owner: Any = user
        if owner is None:
            owner = asyncio.run(store.owner_of(parsed))
            if owner is None:
                console.print(f"[red]no personal access token with id {parsed}[/]")
                raise typer.Exit(code=1)
        removed = asyncio.run(store.revoke(parsed, owner))
        if not removed:
            console.print(f"[red]no personal access token with id {parsed} for user {owner}[/]")
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/] revoked token {parsed} (user {owner})")
        return

    if user is not None:
        # Narrower than --all when both are given: least destruction wins.
        removed = asyncio.run(store.revoke_all_for_user(user))
        if removed:
            console.print(f"[green]✓[/] revoked {removed} token(s) for user {user}")
        else:
            console.print(f"[dim]user {user} has no personal access tokens[/]")
        return

    removed = asyncio.run(store.revoke_all())
    console.print(f"[green]✓[/] revoked {removed} token(s) — every user")


def _accounts_repository() -> Any:
    """The project's accounts repository (tests swap this seam out)."""
    import importlib
    import sys

    from fastplace.cli.generators import _project_root
    from fastplace.queue import _evict_stale_app_modules

    root = _project_root()
    root_str = str(root)
    # Scope the path entry to this call — the import_jobs convention: leaving
    # it behind would hijack the next `import app` elsewhere in the process.
    inserted = root_str not in sys.path
    if inserted:
        sys.path.insert(0, root_str)
    _evict_stale_app_modules(root)
    try:
        module = importlib.import_module("app.modules.accounts.repositories.user_repository")
    finally:
        if inserted:
            sys.path.remove(root_str)
    return module.UserRepository()


@provisioning_app.command("user:create")
def user_create(
    name: str | None = typer.Option(
        None, "--name", help="The user's display name (prompted when omitted)."
    ),
    email: str | None = typer.Option(
        None, "--email", help="The user's email address (prompted when omitted)."
    ),
    password: str | None = typer.Option(
        None, "--password", help="The plaintext password (prompted hidden when omitted)."
    ),
) -> None:
    """Create an account through the project's accounts repository."""
    load_env()

    if name is None:
        name = typer.prompt("Name")
    if email is None:
        email = typer.prompt("Email")
    if password is None:
        # Hidden prompt — the password never lands in terminal scrollback.
        password = typer.prompt("Password", hide_input=True)

    name = name.strip()
    email = email.strip().lower()

    if not name:
        console.print("[red]the name cannot be empty[/]")
        raise typer.Exit(code=1)
    if "@" not in email:
        console.print(f"[red]'{email}' is not a valid email address[/]")
        raise typer.Exit(code=1)
    if not password:
        console.print("[red]the password cannot be empty[/]")
        raise typer.Exit(code=1)

    try:
        repository = _accounts_repository()
    except ImportError:
        console.print(
            "[red]this project has no accounts module[/] — scaffold one with "
            "[bold]fastplace make:auth[/]"
        )
        raise typer.Exit(code=1) from None

    # The duplicate check mirrors RegistrationService: same field, same
    # normalized form the repository will store.
    if asyncio.run(repository.find_by_email(email)) is not None:
        console.print(f"[red]a user with email {email} already exists[/]")
        raise typer.Exit(code=1)

    user = asyncio.run(repository.create_user(name=name, email=email, password=password))
    console.print(f"[green]✓[/] user created: {user.name} <{user.email}> (id {user.id})")
