"""App-plane auth commands: tokens, gates, sessions, 2FA, password resets."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from fastplace.console import console

if TYPE_CHECKING:  # annotations stay lazy; runtime imports remain function-local
    from fastplace.cli._doctor import Check, Status

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

    if driver == "cookie":
        console.print(
            "[dim]session driver 'cookie' is stateless — the payload lives in each "
            "browser's encrypted cookie, so there is nothing server-side to "
            "enumerate (auth:logout-everywhere cannot revoke these either; rotate "
            "APP_KEY to invalidate every outstanding cookie session)[/]"
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
    # APP_URL must resolve before anything mutates: a link minted against an
    # empty origin would kill the prior link and be unusable anyway.
    from fastplace.errors import ConfigurationError
    from fastplace.http import build_absolute_url

    try:
        build_absolute_url("/reset-password/")
    except ConfigurationError:
        console.print("[red]APP_URL is not set[/] — set it before issuing reset links")
        raise typer.Exit(code=1) from None

    driver = str(config("MAIL_DRIVER", default="log") or "log").strip().lower()
    # --send through smtp is real mail in every environment — it gates the
    # confirmation alongside production, so local never means silent sends.
    if (
        (send and driver == "smtp")
        or str(config("APP_ENV", default="production")).lower() == "production"
    ) and not (
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

    try:
        url = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — failures report as one red line, not a traceback
        console.print(f"[red]{type(exc).__name__}:[/] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[bold]{url}[/]")
    console.print("[dim]any previously issued reset link for this address is now invalid[/]")
    if send:
        console.print(
            "[dim]reset mail dispatched through the active transport "
            "(queued when smtp+saq — run queue:work)[/]"
        )


# ---------------------------------------------------------------------------
# auth:doctor — roadmap app plane doctor
# ---------------------------------------------------------------------------


def _check_app_key() -> Check:
    """APP_KEY present and strong enough to sign session cookies."""
    from fastplace.cli._doctor import Check
    from fastplace.config import config

    status: Status
    detail: str
    key = str(config("APP_KEY", default="") or "")
    env = str(config("APP_ENV", default="local")).lower()
    if not key:
        status, detail = (
            ("fail", "APP_KEY empty in production — session cookies cannot be signed")
            if env == "production"
            else ("warn", "APP_KEY not set — session cookies cannot be signed")
        )
        return Check("app_key", status, detail, "fastplace key:generate")
    if len(key) < 32:
        return Check(
            "app_key",
            "warn",
            f"{len(key)} bytes — below the 32-byte HMAC threshold",
            "fastplace key:generate --force",
        )
    return Check("app_key", "pass", f"set ({len(key)} bytes)")


def _check_session_driver() -> Check:
    """SESSION_DRIVER resolves; silent in-memory fallback in production is a WARN."""
    from fastplace.cli._doctor import Check
    from fastplace.config import config
    from fastplace.errors import ConfigurationError
    from fastplace.http.session import session_store

    try:
        store = session_store()
    except ConfigurationError as exc:
        return Check(
            "session_driver",
            "fail",
            str(exc),
            "set SESSION_DRIVER to database, redis, or memory in .env",
        )
    driver = type(store).__name__.removesuffix("SessionStore").lower()
    env = str(config("APP_ENV", default="local")).lower()
    if driver == "memory" and env == "production":
        return Check(
            "session_driver",
            "warn",
            "memory sessions in production — auth:logout-everywhere destroys nothing",
            "set SESSION_DRIVER=database (or redis) in .env",
        )
    return Check("session_driver", "pass", driver)


def _check_auth_windows() -> Check:
    """Throttle + expiry knobs stay inside defensible windows."""
    from fastplace.cli._doctor import Check
    from fastplace.config import config

    warnings: list[str] = []
    expire = int(config("AUTH_PASSWORD_EXPIRE", default=60) or 0)
    throttle = int(config("AUTH_RESET_THROTTLE", default=60) or 0)
    attempts = int(config("AUTH_LOGIN_MAX_ATTEMPTS", default=5) or 0)
    if expire > 120:
        warnings.append(f"AUTH_PASSWORD_EXPIRE={expire}m — reset tokens live over 2h")
    if throttle < 30:
        warnings.append(f"AUTH_RESET_THROTTLE={throttle}s — under 30s invites reset-email flooding")
    if attempts < 3:
        warnings.append(f"AUTH_LOGIN_MAX_ATTEMPTS={attempts} — lockout kicks in too early")
    if warnings:
        return Check(
            "auth_windows", "warn", "; ".join(warnings), "tighten the listed knobs in .env"
        )
    return Check(
        "auth_windows",
        "pass",
        f"reset {expire}m / throttle {throttle}s / lockout after {attempts} attempts",
    )


#: Framework auth ledger tables the doctor probes (created lazily on first
#: use — absence is a heads-up, not an error; unreachability is).
_AUTH_TABLES = (
    "personal_access_tokens",
    "password_reset_tokens",
    "remember_tokens",
    "sessions",
)


def _check_auth_tables() -> Check:
    """PAT / reset / remember / session tables are reachable on the default DB."""
    import asyncio

    from fastplace.cli._doctor import Check

    async def _probe() -> list[str]:
        from sqlalchemy import inspect as sa_inspect

        from fastplace.db import db

        engine = db.manager.engine("default")
        try:
            async with engine.connect() as connection:
                names = await connection.run_sync(lambda conn: sa_inspect(conn).get_table_names())
        finally:
            await engine.dispose()
        return names

    try:
        existing = asyncio.run(_probe())
    except Exception as exc:  # noqa: BLE001 — unreachability is itself the finding
        return Check(
            "auth_tables",
            "fail",
            f"cannot probe the database: {type(exc).__name__}",
            "run fastplace db:health — the database must be reachable",
        )
    missing = [name for name in _AUTH_TABLES if name not in existing]
    if missing:
        return Check(
            "auth_tables",
            "warn",
            f"created on first use: {', '.join(missing)}",
            "no action — tables appear when first written",
        )
    return Check("auth_tables", "pass", "4/4 present")


@auth_ops_app.command("auth:doctor")
def auth_doctor() -> None:
    """Auth stack diagnosis: APP_KEY, session driver, windows, ledger tables."""

    from fastplace.cli._doctor import run_checks
    from fastplace.cli.system import _project_root
    from fastplace.config import load_env, reset_config

    root = _project_root()
    load_env(root / ".env")
    # Bind the config registry to the invoked project (doctor umbrella pattern).
    reset_config(root)

    code = run_checks(
        "Auth doctor",
        [_check_app_key, _check_session_driver, _check_auth_windows, _check_auth_tables],
    )
    raise typer.Exit(code=code)
