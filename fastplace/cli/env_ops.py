"""Config/env/ops commands: the doctor umbrella pre-flight."""

from __future__ import annotations

import os
import re
from pathlib import Path

import typer

from fastplace.cli._doctor import Check, CheckFunc, run_checks
from fastplace.console import console

env_ops_app = typer.Typer(help="Environment and operations diagnostics.")


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


_ACTIVE_KEY_RE = re.compile(r"^\s*([A-Z0-9_]+)\s*=", re.MULTILINE)


def _active_env_keys(text: str) -> list[str]:
    """Active (uncommented) KEY= names in .env text, in order."""
    return [m.group(1) for m in _ACTIVE_KEY_RE.finditer(text)]


def _env_file_check(root: Path) -> CheckFunc:
    def _env_file() -> Check:
        env_path = root / ".env"
        if not env_path.is_file():
            return Check("env-file", "pass", detail="no .env — defaults only")
        text = env_path.read_text(encoding="utf-8")
        seen: set[str] = set()
        dupes: set[str] = set()
        for key in _active_env_keys(text):
            if key in seen:
                dupes.add(key)
            seen.add(key)
        if dupes:
            return Check(
                "env-file",
                "fail",
                detail=f"duplicate active keys: {', '.join(sorted(dupes))} (last wins)",
                fix="remove the earlier duplicate lines in .env",
            )
        return Check("env-file", "pass", detail=f"{len(seen)} keys, no duplicates")

    return _env_file


def _app_key_check() -> CheckFunc:
    from fastplace.config import config

    def _app_key() -> Check:
        key = str(config("APP_KEY", default="") or "")
        env = str(config("APP_ENV", default="production")).lower()
        if not key:
            if env == "production":
                return Check(
                    "app-key", "fail", detail="empty in production (boot refuses)",
                    fix="fastplace key:generate",
                )
            return Check("app-key", "warn", detail="not set", fix="fastplace key:generate")
        if len(key) < 32:
            return Check(
                "app-key",
                "warn",
                detail=f"{len(key)} bytes — below the 32-byte HMAC threshold",
                fix="fastplace key:generate --force",
            )
        return Check("app-key", "pass", detail=f"set ({len(key)} bytes)")

    return _app_key


def _config_import_check(root: Path) -> CheckFunc:
    from fastplace.config import Config

    def _config_import() -> Check:
        try:
            Config(root).load()
        except (ImportError, SyntaxError) as exc:
            return Check(
                "config-import", "fail", detail=f"{type(exc).__name__}: {exc}"
            )
        return Check("config-import", "pass", detail="config/*.py import clean")

    return _config_import


def _env_types_check(root: Path) -> CheckFunc:
    from fastplace.config import Config, _coerce

    def _env_types() -> Check:
        cfg = Config(root)
        cfg.load()
        bad: list[str] = []
        checked = 0
        for key, default in sorted(cfg._defaults.items()):
            if "." in key or default is None or key not in os.environ:
                continue
            checked += 1
            if type(_coerce(os.environ[key], default)) is not type(default):
                bad.append(key)
        env_path = root / ".env"
        active: set[str] = set()
        if env_path.is_file():
            active = set(_active_env_keys(env_path.read_text(encoding="utf-8")))
        typed = {k for k in cfg._defaults if "." not in k}
        # Documentation-only knobs (VITE_PORT, QUERY_*) are deliberately
        # untyped — listing them as such is noise, not a finding.
        untyped = sorted(k for k in active - typed if not _is_example_only(k))
        detail = f"{checked} typed overrides ok" if not bad else "coerce failed: " + ", ".join(bad[:6])
        if untyped:
            detail += f"; untyped: {', '.join(untyped[:6])}"
        return Check(
            "env-types",
            "warn" if bad else "pass",
            detail=detail,
            fix="fix the listed .env values to match their config type" if bad else "",
        )

    return _env_types


def _database_check() -> CheckFunc:
    def _database() -> Check:
        import asyncio

        from sqlalchemy import text

        from fastplace.orm.manager import get_manager, reset_manager

        async def _probe() -> None:
            engine = get_manager().engine("default")
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            finally:
                await engine.dispose()

        try:
            asyncio.run(_probe())
        except Exception as exc:  # noqa: BLE001 - report, never crash the table
            return Check(
                "database",
                "fail",
                detail=type(exc).__name__,
                fix="check DATABASE_URL and that the database is running",
            )
        finally:
            reset_manager()
        return Check("database", "pass", detail="SELECT 1 ok")

    return _database


def _redis_check() -> CheckFunc:
    def _redis() -> Check:
        import asyncio

        from fastplace.config import config

        wanted: list[tuple[str, str]] = []
        if str(config("CACHE_DRIVER", default="memory")).lower() == "redis":
            wanted.append(("cache", str(config("REDIS_URL", default="redis://localhost:6379/0"))))
        if str(config("SESSION_DRIVER", default="")).lower() == "redis":
            wanted.append(("session", str(config("REDIS_URL", default="redis://localhost:6379/0"))))
        if str(config("QUEUE_DRIVER", default="memory")).lower() == "saq":
            wanted.append(("queue", str(config("QUEUE_REDIS_URL", default="redis://localhost:6379/0"))))
        if not wanted:
            return Check("redis", "pass", detail="not used (no redis driver configured)")
        try:
            import redis.asyncio as aioredis
        except ImportError:
            return Check(
                "redis", "fail", detail="redis library not installed",
                fix="pip install 'fastplace[queue]'",
            )

        async def _ping(url: str) -> None:
            client = aioredis.from_url(url)
            try:
                await client.ping()
            finally:
                await client.aclose()

        for label, url in wanted:
            try:
                asyncio.run(_ping(url))
            except Exception as exc:  # noqa: BLE001
                return Check(
                    "redis", "fail",
                    detail=f"{label} unreachable ({type(exc).__name__})",
                    fix="check the redis URL and that redis is running",
                )
        return Check("redis", "pass", detail=f"{', '.join(label for label, _ in wanted)} reachable")

    return _redis


def _mail_check() -> CheckFunc:
    from fastplace.config import config

    def _mail() -> Check | list[Check]:
        driver = str(config("MAIL_DRIVER", default="log")).lower()
        if driver != "smtp":
            return Check("mail", "pass", detail=f"driver={driver} (no smtp credentials needed)")
        missing = [
            name
            for name in ("MAIL_HOST", "MAIL_USERNAME", "MAIL_PASSWORD")
            if not str(config(name, default="") or "")
        ]
        if missing:
            return Check(
                "mail", "fail", detail=f"missing: {', '.join(missing)}",
                fix="set MAIL_HOST / MAIL_USERNAME / MAIL_PASSWORD in .env",
            )
        if str(config("QUEUE_DRIVER", default="memory")).lower() == "saq":
            return [
                Check("mail", "pass", detail="smtp credentials present"),
                Check(
                    "mail-queue", "warn",
                    detail="smtp mail is queued — it needs a running worker",
                    fix="fastplace queue:work",
                ),
            ]
        return Check("mail", "pass", detail="smtp credentials present")

    return _mail


def _ai_keys_check() -> CheckFunc:
    from fastplace.config import config

    def _ai_keys() -> Check:
        model = str(config("AI_MODEL", default="")).lower()
        if "gpt" in model or "openai" in model:
            key = "OPENAI_API_KEY"
        elif "claude" in model or "anthropic" in model:
            key = "ANTHROPIC_API_KEY"
        else:
            present = [k for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY") if os.environ.get(k)]
            if present:
                return Check("ai-keys", "pass", detail=f"{', '.join(present)} set")
            return Check(
                "ai-keys", "warn", detail="no provider key in environment",
                fix="export OPENAI_API_KEY or ANTHROPIC_API_KEY",
            )
        if os.environ.get(key):
            return Check("ai-keys", "pass", detail=f"{key} set")
        return Check("ai-keys", "warn", detail=f"{key} not set", fix=f"export {key}")

    return _ai_keys


def _storage_check(root: Path) -> CheckFunc:
    def _storage() -> Check | list[Check]:
        rows: list[Check] = []
        for name in ("storage/logs", "storage/framework"):
            target = root / name
            try:
                target.mkdir(parents=True, exist_ok=True)
                probe = target / ".doctor-probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
            except OSError as exc:
                rows.append(
                    Check(f"writable:{name}", "fail", detail=type(exc).__name__,
                          fix=f"check permissions on {name}")
                )
                continue
            rows.append(Check(f"writable:{name}", "pass"))
        return rows

    return _storage


def _serve_check(root: Path) -> CheckFunc:
    def _serve() -> Check | list[Check]:
        if not (root / "package.json").is_file():
            return Check("serve-ready", "pass", detail="no frontend (package.json absent)")
        from fastplace.http.assets import _manifest_path

        rows: list[Check] = []
        manifest = _manifest_path(root)
        rel = manifest.relative_to(root) if manifest else None
        rows.append(
            Check(
                "build-manifest", "pass" if manifest else "warn",
                detail=str(rel) if rel else "missing",
                fix="" if manifest else "npm run build",
            )
        )
        if not (root / "node_modules").is_dir():
            rows.append(Check("node-modules", "warn", detail="absent", fix="npm install"))
        return rows if len(rows) > 1 else rows[0]

    return _serve


_EXAMPLE_ONLY_KEYS = {"VITE_PORT", "ASSET_VERSION"}
_EXAMPLE_ONLY_PREFIXES = ("QUERY_",)


def _is_example_only(key: str) -> bool:
    return key in _EXAMPLE_ONLY_KEYS or key.startswith(_EXAMPLE_ONLY_PREFIXES)


_ENV_PAIR_RE = re.compile(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", re.MULTILINE)


def _env_values(text: str) -> dict[str, str]:
    """Active KEY=value pairs (last wins, mirroring dotenv semantics)."""
    values: dict[str, str] = {}
    for m in _ENV_PAIR_RE.finditer(text):
        key = m.group(1)
        raw = m.group(2).strip().strip("'\"")
        values[key] = raw
    return values


def _secret_looking(value: str) -> bool:
    """Heuristic: long + high-entropy. Never printed either way."""
    return len(value) >= 32 and len(set(value)) >= 10


def _duplicate_check(env_keys: list[str]) -> CheckFunc:
    def _dup() -> Check:
        seen: set[str] = set()
        dupes: set[str] = set()
        for key in env_keys:
            if key in seen:
                dupes.add(key)
            seen.add(key)
        if dupes:
            return Check(
                "duplicates", "fail",
                detail=f"duplicate active keys: {', '.join(sorted(dupes))} (last wins)",
                fix="remove the earlier duplicate lines",
            )
        return Check("duplicates", "pass")

    return _dup


def _example_drift_check(env_keys: list[str], example_keys: set[str]) -> CheckFunc:
    def _drift() -> Check:
        undocumented = sorted(
            k for k in set(env_keys) - example_keys if not _is_example_only(k)
        )
        if undocumented:
            return Check(
                "example-drift", "warn",
                detail=f"in .env but not .env.example: {', '.join(undocumented[:8])}",
                fix="fastplace env:lint --fix (documents them) or remove them",
            )
        return Check("example-drift", "pass", detail="documented")

    return _drift


def _example_secrets_check(example_text: str, live_values: dict[str, str]) -> CheckFunc:
    def _secrets() -> Check:
        flagged: list[str] = []
        for key, value in _env_values(example_text).items():
            if _secret_looking(value) or (key in live_values and value == live_values[key] and value):
                flagged.append(key)
        # commented example values count too — secrets must not sit in example
        # files. Horizontal whitespace only: \s here would cross newlines and
        # stitch "# KEY=" onto the following line as a fake value. \r is
        # tolerated right before end-of-line so a CRLF example file cannot
        # dodge the match.
        for m in re.finditer(r"^[ \t]*#[ \t]*([A-Z0-9_]+)[ \t]*=[ \t]*(\S+)[ \t\r]*$", example_text, re.MULTILINE):
            key, value = m.group(1), m.group(2)
            if _secret_looking(value) or (key in live_values and value == live_values[key] and value):
                if key not in flagged:
                    flagged.append(key)
        if flagged:
            return Check(
                "example-secrets", "fail",
                detail=f"secret-looking values in .env.example: {', '.join(sorted(flagged))}",
                fix="replace with empty placeholders (# KEY=)",
            )
        return Check("example-secrets", "pass")

    return _secrets


def _production_placeholder_check() -> CheckFunc:
    from fastplace.config import config

    def _prod() -> Check:
        env = str(config("APP_ENV", default="production")).lower()
        if env != "production":
            return Check("prod-placeholders", "pass", detail=f"env={env}")
        key = str(config("APP_KEY", default="") or "")
        if not key:
            return Check(
                "prod-placeholders", "fail",
                detail="APP_ENV=production with an empty APP_KEY",
                fix="fastplace key:generate",
            )
        return Check("prod-placeholders", "pass", detail="production key set")

    return _prod


@env_ops_app.command("env:lint")
def env_lint(
    fix: bool = typer.Option(False, "--fix", help="Sync .env.example placeholders (never touches .env values)."),
) -> None:
    """Audit .env against .env.example: duplicates, drift, leaked secrets, coercion."""
    from fastplace.cli.database import _ensure_example_keys
    from fastplace.config import load_env, reset_config

    root = _project_root()
    env_path = root / ".env"
    example_path = root / ".env.example"
    if not env_path.is_file():
        console.print("[red]no .env to lint[/] — run this from a project root with a .env.")
        raise typer.Exit(code=1)

    env_text = env_path.read_text(encoding="utf-8")
    example_text = example_path.read_text(encoding="utf-8") if example_path.is_file() else ""

    load_env(root / ".env")
    reset_config(root)

    if fix:
        # Empty placeholders only: copying live values would move real
        # secrets into a file meant to be committed.
        _ensure_example_keys(example_path, dict.fromkeys(_env_values(env_text), ""))
        example_text = example_path.read_text(encoding="utf-8") if example_path.is_file() else ""
        console.print("[green].env.example synced[/] (.env values untouched).")

    env_keys = _active_env_keys(env_text)
    example_keys = set(_active_env_keys(example_text)) | set(_env_values(example_text))
    code = run_checks(
        "env lint",
        [
            _duplicate_check(env_keys),
            _example_drift_check(env_keys, example_keys),
            _example_secrets_check(example_text, _env_values(env_text)),
            _env_types_check(root),
            _production_placeholder_check(),
        ],
    )
    raise typer.Exit(code=code)


_KEY_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _sync_example_key(root: Path, key: str) -> bool:
    """Document an absent key in .env.example as '# KEY=' (empty placeholder).

    The placeholder is ALWAYS empty — live values never cross into the
    example file, secret-looking or not. Returns True when appended.
    """
    example = root / ".env.example"
    text = example.read_text(encoding="utf-8") if example.is_file() else ""
    if re.search(rf"^\s*#?\s*{re.escape(key)}\s*=", text, re.MULTILINE):
        return False
    with example.open("a", encoding="utf-8") as fh:
        fh.write(f"# {key}=\n")
    return True


@env_ops_app.command("env:set")
def env_set(
    key: str = typer.Argument(..., help="Variable name (UPPER_SNAKE)."),
    value: str = typer.Argument(..., help="Variable value, written verbatim."),
) -> None:
    """Set KEY=VALUE in .env without an editor; idempotent, never touches APP_KEY."""
    from fastplace.cli.database import _upsert_env_lines

    root = _project_root()

    if key == "APP_KEY":
        console.print(
            "[red]refusing to set APP_KEY by hand[/] — use "
            "[bold]fastplace key:generate --force[/] so backups, .env.encrypted "
            "re-encryption, and the rotation impact report all happen."
        )
        raise typer.Exit(code=1)

    if not _KEY_NAME_RE.match(key):
        console.print(
            f"[red]{key!r} is not a valid variable name[/] — UPPER_SNAKE (A-Z, 0-9, _; starts with a letter)."
        )
        raise typer.Exit(code=1)

    env_path = root / ".env"
    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""

    # Idempotency pre-check: exact active value already set — no rewrite, mtime preserved.
    if _env_values(text).get(key) == value:
        console.print(f"{key} already [green]{value}[/] — unchanged.")
        raise typer.Exit(code=0)

    was_commented = re.search(rf"^\s*#\s*{re.escape(key)}\s*=", text, re.MULTILINE) is not None

    env_path.write_text(_upsert_env_lines(text, {key: value}), encoding="utf-8")

    if was_commented:
        console.print(f"[yellow]ACTIVATED[/] {key} — it was commented out in .env; the live line now wins.")
    console.print(f"{key} set in .env.")

    if _sync_example_key(root, key):
        console.print(f"[dim]{key} documented in .env.example as an empty placeholder.[/]")


@env_ops_app.command("doctor")
def doctor() -> None:
    """Project-wide pre-flight: env, config, DB, redis, mail, storage, serve readiness."""
    from fastplace.config import load_env, reset_config

    root = _project_root()
    load_env(root / ".env")
    reset_config(root)

    code = run_checks(
        "Fastplace doctor",
        [
            _env_file_check(root),
            _app_key_check(),
            _config_import_check(root),
            _env_types_check(root),
            _database_check(),
            _redis_check(),
            _mail_check(),
            _ai_keys_check(),
            _storage_check(root),
            _serve_check(root),
        ],
    )
    raise typer.Exit(code=code)


def _log_inventory(root: Path) -> list[tuple[Path, int]]:
    """(path, size-bytes) for every *.log under storage/logs, newest-last.

    Stat races tolerated: a log rotated away between glob and stat is
    skipped, never fatal.
    """
    logs_dir = root / "storage" / "logs"
    if not logs_dir.is_dir():
        return []
    inventory: list[tuple[Path, int]] = []
    for path in sorted(logs_dir.glob("*.log")):
        try:
            inventory.append((path, path.stat().st_size))
        except OSError:
            continue  # rotated away mid-scan
    return inventory


@env_ops_app.command("log:prune")
def log_prune(
    days: float | None = typer.Option(
        None, "--days",
        help="Delete whole log FILES whose mtime is older than N days (file granularity — "
             "never partial files). Omit --days to list only, deleting nothing.",
    ),
    force: bool = typer.Option(False, "--force", help="Skip the production confirmation."),
) -> None:
    """Inventory storage/logs/*.log; with --days, remove the stale ones. Content is never printed."""
    import time

    from fastplace.config import config, load_env, reset_config

    root = _project_root()
    load_env(root / ".env")
    reset_config(root)

    inventory = _log_inventory(root)
    if not inventory:
        console.print("no log files under storage/logs — nothing to prune.")
        raise typer.Exit(code=0)

    total = sum(size for _, size in inventory)
    console.print(f"{len(inventory)} log file(s), {total} bytes under storage/logs:")
    for path, size in inventory:
        try:
            age_days = (time.time() - path.stat().st_mtime) / 86400
        except OSError:
            age_days = -1
        console.print(f"  {path.name}  {size} bytes  ({age_days:.0f}d old)")

    if days is None:
        console.print("[dim]list-only — pass --days N (and --force in production) to remove stale files.[/]")
        raise typer.Exit(code=0)

    if str(config("APP_ENV", default="production")).lower() == "production" and not (
        force or typer.confirm(f"Delete log files older than {days:g} days in production?")
    ):
        console.print("[red]aborted[/]")
        raise typer.Exit(code=1)

    removed = 0
    freed = 0
    for path, size in inventory:
        try:
            age_days = (time.time() - path.stat().st_mtime) / 86400
        except OSError:
            continue
        if age_days < days:
            continue
        try:
            path.unlink()
        except OSError:
            continue  # vanished or locked — report what actually went
        removed += 1
        freed += size

    console.print(f"[green]removed {removed} log file(s)[/], freed {freed} bytes.")
