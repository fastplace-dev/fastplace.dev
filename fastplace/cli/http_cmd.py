"""HTTP runtime commands: doctor, check, middleware listing, ad-hoc requests."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import typer

from fastplace.cli._doctor import Check, run_checks
from fastplace.console import console

http_app = typer.Typer(help="HTTP runtime diagnostics and probes.")


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


def _env_value(root: Path, key: str, default: str = "") -> str:
    """Read KEY from .env (active lines) — http:doctor never boots the app."""
    from fastplace.config import load_env

    load_env(root / ".env")
    return os.environ.get(key, default)


def _writable_probe(target: Path, name: str) -> Check:
    """mkdir+write+unlink probe; absent dir = healthy-empty (fresh clones)."""
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".http-doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check(name, "fail", detail=type(exc).__name__,
                     fix=f"check permissions on {target.name}")
    return Check(name, "pass")


def _app_key_check() -> Check:
    from fastplace.config import config

    key = str(config("APP_KEY", default="") or "")
    env = str(config("APP_ENV", default="production")).lower()
    if env == "production" and not key:
        return Check(
            "app-key", "fail",
            detail="empty in production — boot refuses (kernel gate)",
            fix="fastplace key:generate",
        )
    if not key:
        return Check("app-key", "warn", detail="not set", fix="fastplace key:generate")
    if len(key) < 32:
        return Check(
            "app-key", "warn",
            detail=f"{len(key)} bytes — below the 32-byte HMAC threshold",
            fix="fastplace key:generate --force",
        )
    return Check("app-key", "pass", detail=f"set ({len(key)} bytes)")


def _app_url_check() -> Check:
    from fastplace.config import config

    raw = str(config("APP_URL", default="") or "")
    trusted = str(config("TRUSTED_HOSTS", default="") or "")
    env = str(config("APP_ENV", default="production")).lower()
    if not raw:
        if trusted:
            return Check(
                "app-url", "warn",
                detail="APP_URL empty; TRUSTED_HOSTS set — request-ful URLs only",
                fix="set APP_URL=https://your.domain",
            )
        if env != "production":
            # A fresh local scaffold ships both empty; that must not fail the
            # doctor. It still raises for links, so surface it as a warning.
            return Check(
                "app-url", "warn",
                detail="APP_URL and TRUSTED_HOSTS both empty — absolute links raise",
                fix="set APP_URL=https://your.domain",
            )
        return Check(
            "app-url", "fail",
            detail="APP_URL and TRUSTED_HOSTS both empty — build_absolute_url raises",
            fix="set APP_URL (or TRUSTED_HOSTS) in .env",
        )
    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return Check("app-url", "pass", detail=parsed.netloc)
    return Check(
        "app-url", "fail",
        detail="set but unparseable — needs scheme://netloc",
        fix="set APP_URL=https://your.domain",
    )


def _maintenance_check(root: Path) -> Check:
    from fastplace.http.maintenance import MAINTENANCE_FILE, is_down

    state_path = root / MAINTENANCE_FILE
    if not state_path.exists():
        return Check("maintenance", "pass", detail="up (no state file)")
    # is_down swallows OSError itself and reports it as ``None`` — the branch
    # below already carries the present-but-unreadable distinction.
    state = is_down(root)
    if state is None:
        return Check(
            "maintenance", "fail",
            detail="state file present but unreadable — gate silently re-opens",
            fix=f"fix permissions on {MAINTENANCE_FILE}",
        )
    if state == {}:
        return Check(
            "maintenance", "warn",
            detail="down, state unreadable — fail-closed (503 for everyone)",
            fix="fastplace up to reset, or fix the state file JSON",
        )
    bits = [f"down (retry={state.get('retry')}, refresh={state.get('refresh')}"]
    bits.append(f"secret={'yes' if state.get('secret') else 'no'})")
    return Check("maintenance", "pass", detail=" ".join(bits))


def _manifest_check(root: Path) -> Check:
    from fastplace.http.assets import _manifest_path

    manifest = _manifest_path(root)
    if manifest:
        return Check("manifest", "pass", detail=str(manifest.relative_to(root)))
    if (root / "package.json").is_file():
        return Check(
            "manifest", "warn", detail="missing (package.json present)",
            fix="npm run build",
        )
    return Check("manifest", "pass", detail="no frontend")


def _node_modules_check(root: Path) -> Check:
    if not (root / "package.json").is_file():
        return Check("node-modules", "pass", detail="no frontend")
    if (root / "node_modules").is_dir():
        return Check("node-modules", "pass")
    return Check("node-modules", "warn", detail="absent", fix="npm install")


@http_app.command("http:doctor")
def http_doctor() -> None:
    """Boot-time contract without starting a server: key, url, storage, maintenance, manifest."""
    from fastplace.config import load_env, reset_config

    root = _project_root()
    load_env(root / ".env")
    reset_config(root)

    code = run_checks(
        "HTTP doctor (no server)",
        [
            _app_key_check,
            _app_url_check,
            lambda: _writable_probe(root / "storage" / "logs", "writable:storage/logs"),
            lambda: _writable_probe(root / "storage" / "framework", "writable:storage/framework"),
            lambda: _writable_probe(root / "public" / "build", "writable:public/build"),
            lambda: _maintenance_check(root),
            lambda: _manifest_check(root),
            lambda: _node_modules_check(root),
        ],
    )
    raise typer.Exit(code=code)
