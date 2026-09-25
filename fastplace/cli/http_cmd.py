"""HTTP runtime commands: doctor, check, middleware listing, ad-hoc requests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
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


def _boot_client(root: Path) -> tuple[Any, Any, Any]:
    """Boot the project's asgi app in-process: (app, LifespanManager, client).

    raise_app_exceptions=False is mandatory — an unhandled 500 surfaces as
    the JSON envelope, never a traceback in the CLI (test_kernel idiom).
    Shared with ``http:request`` (roadmap Task 14).
    """
    import httpx
    from asgi_lifespan import LifespanManager

    from fastplace.cli.inspect import _load_asgi_app

    app = _load_asgi_app(root)
    manager = LifespanManager(app)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://fastplace.local",
    )
    return app, manager, client


def _resolved_env() -> str:
    """Lowercased APP_ENV, conservatively defaulting to production."""
    from fastplace.config import config

    return str(config("APP_ENV", default="production")).lower()


def _session_cookie_check(app: Any, response: Any) -> Check:
    """Set-Cookie flags when a cookie was minted; else the wiring kwargs."""
    env = _resolved_env()
    sc = response.headers.get("set-cookie", "")
    if sc:
        low = sc.lower()
        problems = []
        if "httponly" not in low:
            problems.append("HttpOnly missing")
        if "samesite=lax" not in low:
            problems.append("SameSite != Lax")
        secure = "secure" in low
        if env == "production" and not secure:
            problems.append("Secure missing in production")
        if env != "production" and secure:
            problems.append("Secure set outside production (harmless but unusual)")
        return Check(
            "session-cookie", "fail" if problems else "pass",
            detail="; ".join(problems) or "flags ok",
        )
    # No cookie minted by this probe — assert the middleware wiring instead.
    # ServerSessionMiddleware hardcodes SameSite=Lax in _cookie_header and
    # takes `secure` from the kernel wiring, so the kwargs carry the truth.
    for entry in app.user_middleware:
        if entry.cls.__name__ != "ServerSessionMiddleware":
            continue
        kwargs = entry.kwargs or {}
        samesite = str(kwargs.get("samesite", "lax")).lower()
        secure = bool(kwargs.get("secure", False)) or bool(kwargs.get("https_only", False))
        problems = []
        if samesite != "lax":
            problems.append(f"SameSite={samesite}")
        if env == "production" and not secure:
            problems.append("Secure not wired for production")
        return Check(
            "session-cookie", "fail" if problems else "pass",
            detail="; ".join(problems) or "wiring ok (no cookie minted by probe)",
        )
    return Check(
        "session-cookie", "fail",
        detail="ServerSessionMiddleware not installed",
        fix="check the app's middleware stack",
    )


def _gate_check(*, down: bool, clone_503: bool | None) -> Check:
    """Verdict for the 503 gate: live DOWN state warns; else the clone probe decides."""
    if down:
        return Check(
            "503-gate", "warn",
            detail="app is currently DOWN — every request answers 503",
            fix="fastplace up (when intentional) or fix the state file",
        )
    ok = bool(clone_503)
    return Check(
        "503-gate", "pass" if ok else "fail",
        detail="clone probe answered 503" if ok else "down clone did NOT answer 503",
    )


async def _clone_gate_probe() -> bool:
    """Boot a throwaway app pointed at a down clone; True when it answers 503.

    Runs inside the command's one event loop — an ``asyncio.run`` here would
    raise (loop already running). ``routes`` stays unset on purpose: the 503
    gate is the outermost middleware and answers before any route resolves,
    so a route-less boot isolates exactly the gate (get_app is keyword-only).
    """
    import shutil
    import tempfile

    import httpx
    from asgi_lifespan import LifespanManager

    from fastplace.http import get_app
    from fastplace.http.maintenance import MAINTENANCE_FILE

    clone = Path(tempfile.mkdtemp(prefix="fastplace-httpcheck-"))
    try:
        state = clone / MAINTENANCE_FILE
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text('{"retry": 30}\n', encoding="utf-8")
        app2 = get_app(project_root=clone)
        async with LifespanManager(app2):
            transport = httpx.ASGITransport(app=app2, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://fastplace.local"
            ) as clone_client:
                r = await clone_client.get("/")
                return r.status_code == 503
    finally:
        shutil.rmtree(clone, ignore_errors=True)


def _http_check_run(root: Path) -> int:
    """Run the five hardening probes against the live app; return the exit code."""

    async def _run() -> int:
        from fastplace.http.kernel import _SECURITY_HEADERS
        from fastplace.http.maintenance import is_down

        app, manager, client = _boot_client(root)
        checks: list = []
        down = is_down(root) is not None
        async with manager:
            async with client:
                # 1. security headers — kernel values, compared exactly.
                probe = await client.get("/__fastplace_check_missing__")
                missing = [
                    name.decode("ascii")
                    for name, value in _SECURITY_HEADERS
                    if probe.headers.get(name.decode("ascii").lower())
                    != value.decode("ascii")
                ]
                checks.append(
                    lambda: Check(
                        "security-headers",
                        "fail" if missing else "pass",
                        detail=(
                            f"missing: {', '.join(missing)}"
                            if missing
                            else f"{len(_SECURITY_HEADERS)} headers ok"
                        ),
                    )
                )
                # 2. session cookie flags / wiring
                checks.append(lambda: _session_cookie_check(app, probe))
                # 3. live 503-gate state + clone probe (eager: one event loop)
                clone_503: bool | None = None if down else await _clone_gate_probe()
                checks.append(lambda: _gate_check(down=down, clone_503=clone_503))
                # 4. JSON error shape — a DOWN app answers the gate page, so
                # the envelope is unverifiable, not broken.
                json_ok = False
                if not down:
                    try:
                        body = probe.json()
                        json_ok = isinstance(body, dict) and "message" in body
                    except Exception:  # noqa: BLE001 - HTML/plain bodies are the failure
                        json_ok = False
                checks.append(
                    lambda: Check(
                        "json-404",
                        "warn" if down else ("pass" if json_ok else "fail"),
                        detail=(
                            "unverifiable — app answers the 503 gate"
                            if down
                            else (
                                "404 body is JSON with message"
                                if json_ok
                                else "404 body not the JSON envelope"
                            )
                        ),
                    )
                )
                # 5. /api/docs gating — gated (404) in production, open (200) otherwise
                env = _resolved_env()
                if down:
                    checks.append(
                        lambda: Check(
                            "api-docs", "warn",
                            detail=f"unverifiable while DOWN (env={env})",
                        )
                    )
                else:
                    docs = await client.get("/api/docs")
                    docs_status = docs.status_code
                    docs_ok = docs_status == 404 if env == "production" else docs_status == 200
                    checks.append(
                        lambda: Check(
                            "api-docs", "pass" if docs_ok else "fail",
                            detail=f"status={docs_status} (env={env})",
                        )
                    )
        return run_checks("HTTP check (in-process)", checks)

    import asyncio

    return asyncio.run(_run())


@http_app.command("http:check")
def http_check() -> None:
    """Boot the app in-process and assert the hardening contract (5 probes)."""
    from fastplace.config import load_env, reset_config

    root = _project_root()
    load_env(root / ".env")
    reset_config(root)
    raise typer.Exit(code=_http_check_run(root))
