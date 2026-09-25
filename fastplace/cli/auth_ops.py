"""App-plane auth commands: tokens, gates, sessions, 2FA, password resets."""

from __future__ import annotations

import typer

from fastplace.console import console

auth_ops_app = typer.Typer(help="Auth & security operations (tokens, gates, sessions, 2FA).")


@auth_ops_app.command("token:list")
def token_list(
    user: int | None = typer.Option(None, "--user", help="Only tokens owned by this user id."),
) -> None:
    """List personal access tokens, newest first."""
    import asyncio

    from fastplace.config import load_env

    load_env()

    async def _run() -> list:
        from fastplace.auth.tokens import pat_store

        if user is not None:
            return await pat_store().list_for_user(user)
        return await pat_store().list_all()

    rows = asyncio.run(_run())
    if not rows:
        scope = f" for user {user}" if user is not None else ""
        console.print(f"[dim]no personal access tokens{scope}[/]")
        return

    from rich.table import Table

    table = Table(title="Personal access tokens")
    for column in ("id", "user", "name", "abilities", "last used", "expires"):
        table.add_column(column)
    for row in rows:
        abilities = ",".join(row.abilities or [])
        table.add_row(
            str(row.id),
            str(row.user_id),
            row.name,
            abilities or "—",
            str(row.last_used_at) if row.last_used_at else "—",
            str(row.expires_at) if row.expires_at else "never",
        )
    console.print(table)


@auth_ops_app.command("gate:check")
def gate_check(
    user_id: int,
    ability: str,
    args: list[str] = typer.Option(
        [], "--arg", help="Gate argument as JSON (repeatable); forwarded in order."
    ),
) -> None:
    """Check one ability for one user.

    JSON args arrive as plain dicts/lists/strings — abilities expecting model
    instances receive them as parsed JSON.
    """
    import asyncio
    import json

    from fastplace.config import load_env
    from fastplace.errors import ConfigurationError

    load_env()
    from fastplace.cli.system import _project_root

    root = _project_root()

    parsed: list = []
    for raw in args:
        try:
            parsed.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            console.print(f"[red]invalid --arg JSON:[/] {raw} ({exc})")
            raise typer.Exit(code=1) from exc

    async def _run() -> dict:
        from fastplace.authz.loader import import_gates

        import_gates(root)
        from fastplace.cli.provisioning import _accounts_repository

        try:
            repository = _accounts_repository()
        except ImportError:
            console.print("[red]no accounts module found[/] — run [bold]make:auth[/] first")
            raise typer.Exit(code=1) from None
        user = await repository.find_by_id(user_id)
        if user is None:
            console.print(f"[red]no user with id {user_id}[/]")
            raise typer.Exit(code=1)
        from fastplace.authz.gate import gate

        return await gate.inspect(user, ability, *parsed)

    try:
        verdict = asyncio.run(_run())
    except ConfigurationError:
        console.print(f"[red]ability '{ability}' is not registered[/]")
        raise typer.Exit(code=1) from None
    mark = "[green]allow[/]" if verdict["allowed"] else "[red]deny[/]"
    console.print(f"{mark}  {verdict['ability']}  [dim](source: {verdict['source']})[/]")
    if verdict.get("message"):
        console.print(f"  {verdict['message']}")
    if not verdict["allowed"]:
        raise typer.Exit(code=1)


def _relative(seconds: int) -> str:
    """Coarse relative age for the last-activity column."""
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


@auth_ops_app.command("auth:sessions")
def auth_sessions(user_id: int) -> None:
    """List a user's active sessions on the configured durable store."""
    import asyncio
    import time

    from fastplace.config import config, load_env

    load_env()
    driver = str(config("SESSION_DRIVER", default="") or "").strip().lower()
    if not driver:
        env = str(config("APP_ENV", default="local")).lower()
        driver = "database" if env == "production" else "memory"

    if driver == "memory":
        console.print(
            "[dim]session driver 'memory' keeps sessions inside the server process — "
            "a CLI process cannot enumerate them (auth:logout-everywhere sees zero "
            "rows on this driver too)[/]"
        )
        return

    async def _run() -> list:
        from typing import cast

        from fastplace.http.session import session_store
        from fastplace.http.session.database import DatabaseSessionStore
        from fastplace.http.session.redis_store import RedisSessionStore

        # sessions_for_user is the durable-store extension (memory opts out);
        # the SessionStore protocol does not declare it — narrow for mypy only.
        store = cast(DatabaseSessionStore | RedisSessionStore, session_store())
        return await store.sessions_for_user(user_id)

    try:
        rows = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — store errors report, not traceback
        console.print(f"[red]session store error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    if not rows:
        console.print(f"[dim]no active sessions for user {user_id}[/]")
        return

    now = int(time.time())
    from rich.table import Table

    table = Table(title=f"Active sessions — user {user_id} ({driver})")
    if driver == "redis":
        for column in ("session", "last activity"):
            table.add_column(column)
        for session_id, last_activity in rows:
            table.add_row(
                str(session_id), f"{_relative(now - int(last_activity))} ({last_activity})"
            )
    else:
        for column in ("session", "last activity", "ip", "user agent"):
            table.add_column(column)
        for row in rows:
            table.add_row(
                str(row.id),
                f"{_relative(now - int(row.last_activity))} ({row.last_activity})",
                row.ip_address or "—",
                row.user_agent or "—",
            )
        console.print("[dim]ip / user agent are not populated by the middleware yet[/]")
    console.print(table)


@auth_ops_app.command("auth:2fa-disable")
def auth_2fa_disable(
    user_id: int,
    force: bool = typer.Option(False, "--force", help="Skip the production confirmation prompt."),
) -> None:
    """Disable two-factor auth for a user and revoke their live logins."""
    import asyncio

    from fastplace.config import config, load_env

    load_env()
    if str(config("APP_ENV", default="production")).lower() == "production" and not (
        force
        or typer.confirm(
            f"Disable two-factor authentication for user {user_id} and revoke their "
            "sessions? The user will need to re-enable 2FA."
        )
    ):
        console.print("[red]aborted[/] — two-factor configuration left untouched")
        raise typer.Exit(code=1)

    async def _run() -> tuple[int, int]:
        from fastplace.auth.remember import remember_store
        from fastplace.cli.provisioning import _accounts_repository
        from fastplace.http.session import session_store

        try:
            repository = _accounts_repository()
        except ImportError:
            console.print("[red]no accounts module found[/] — run [bold]make:auth[/] first")
            raise typer.Exit(code=1) from None
        user = await repository.find_by_id(user_id)
        if user is None:
            console.print(f"[red]no user with id {user_id}[/]")
            raise typer.Exit(code=1)

        columns = ("two_factor_secret", "two_factor_recovery_codes", "two_factor_confirmed_at")
        if all(getattr(user, column, None) is None for column in columns):
            console.print("[dim]no two-factor configuration to clear[/]")
            raise typer.Exit(code=0)
        for column in columns:
            setattr(user, column, None)
        await user.save()
        remembered = await remember_store().revoke_all_for_user(user_id)
        destroyed = await session_store().destroy_for_user(user_id)
        return remembered, destroyed

    remembered, destroyed = asyncio.run(_run())
    console.print(
        "cleared two_factor_secret, two_factor_recovery_codes, "
        f"two_factor_confirmed_at for user {user_id}"
    )
    console.print(f"revoked {remembered} remember token(s); destroyed {destroyed} session(s)")


@auth_ops_app.command("auth:reset-link")
def auth_reset_link(
    user_or_email: str,
    send: bool = typer.Option(False, "--send", help="Also dispatch the reset mail."),
    force: bool = typer.Option(False, "--force", help="Skip the production confirmation prompt."),
) -> None:
    """Reissue a password-reset link (the prior link dies immediately)."""
    import asyncio
    from urllib.parse import quote

    from fastplace.config import config, load_env

    load_env()
    if str(config("APP_ENV", default="production")).lower() == "production" and not (
        force
        or typer.confirm(
            "Reissue the password reset link? The previously issued link stops working immediately."
        )
    ):
        console.print("[red]aborted[/] — the existing reset link is untouched")
        raise typer.Exit(code=1)

    async def _run() -> str:
        from fastplace.auth.passwords import token_store
        from fastplace.cli.provisioning import _accounts_repository
        from fastplace.http import build_absolute_url
        from fastplace.mail.notifications import reset_password_message

        try:
            repository = _accounts_repository()
        except ImportError:
            console.print("[red]no accounts module found[/] — run [bold]make:auth[/] first")
            raise typer.Exit(code=1) from None
        if "@" in user_or_email:
            user = await repository.find_by_email(user_or_email)
        else:
            try:
                user_id = int(user_or_email)
            except ValueError:
                console.print(f"[red]{user_or_email!r} is not a valid user id or email[/]")
                raise typer.Exit(code=1) from None
            user = await repository.find_by_id(user_id)
        if user is None:
            console.print(f"[red]no user matching {user_or_email}[/]")
            raise typer.Exit(code=1)

        raw = await token_store().issue(user.email)
        url = build_absolute_url(f"/reset-password/{raw}?email={quote(user.email, safe='')}")
        if send:
            from fastplace.mail import Mail

            await Mail.to(user.email).send(reset_password_message(user.email, url))
        return url

    url = asyncio.run(_run())
    console.print(f"[bold]{url}[/]")
    console.print("[dim]any previously issued reset link for this address is now invalid[/]")
    if send:
        console.print(
            "[dim]reset mail dispatched through the active transport "
            "(queued when smtp+saq — run queue:work)[/]"
        )
