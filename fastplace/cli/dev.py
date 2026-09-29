"""`fastplace run dev` and `fastplace serve` — server runtimes.

Both entrypoints load ``.env`` before resolving anything (the child server
used to be the only reader, so APP_HOST/APP_PORT in .env were silently
ignored), spawn children in their own process groups, and install
SIGTERM/SIGHUP dispositions so a supervised stop cleans the whole child
tree instead of orphaning it.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from types import FrameType

import typer

from fastplace.console import console

run_app = typer.Typer(help="Server runtimes.")
serve_app = typer.Typer(help="Production serving.")


def _project_root() -> Path:
    return Path(os.getcwd())


def _load_project_env() -> None:
    """Read the project .env into this process before any _cfg resolution."""
    from fastplace.config import load_env

    load_env(_project_root() / ".env")


def _cfg(key: str, default: str) -> str:
    from fastplace.config import config

    return str(config(key, default=default))


def _uvicorn_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "uvicorn", *args]


def _fastplace_bin() -> str | None:
    """The console script next to the running interpreter, else on PATH."""
    sibling = Path(sys.executable).with_name("fastplace")
    if sibling.exists():
        return str(sibling)
    return shutil.which("fastplace")


# ---------------------------------------------------------------------------
# Child-tree supervision
# ---------------------------------------------------------------------------


def _spawn(command: list[str], *, cwd: Path | str, env: dict[str, str]) -> subprocess.Popen:
    """Start a child in its own session so stop signals reach the whole tree.

    ``start_new_session`` detaches the child into a fresh process group:
    the wrapper can then ``killpg`` every descendant (uvicorn reload
    workers, vite, esbuild) without ever signaling itself.
    """
    return subprocess.Popen(
        [str(part) for part in command], cwd=str(cwd), env=env, start_new_session=True
    )


def _signal_group(pid: int, sig: int) -> None:
    """Signal a process group; a no-op where the platform has no killpg."""
    killpg = getattr(os, "killpg", None)
    if killpg is None:  # pragma: no cover — Windows
        return
    try:
        killpg(pid, sig)  # pid == pgid thanks to _spawn
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _terminate_tree(child: subprocess.Popen, *, grace: float = 5) -> None:
    """SIGTERM the child's whole process group, then SIGKILL stragglers."""
    import signal as _signal

    pid = getattr(child, "pid", None)
    group = pid if isinstance(pid, int) and pid > 0 else None
    if group is not None:
        _signal_group(group, _signal.SIGTERM)
    try:
        child.terminate()
    except (ProcessLookupError, OSError):
        pass
    try:
        child.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        if group is not None:
            _signal_group(group, _signal.SIGKILL)
        try:
            child.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            child.wait(timeout=grace)
        except subprocess.TimeoutExpired:  # pragma: no cover — unkillable child
            pass
        return
    # The leader exited on TERM, but descendants that ignored it keep the
    # group alive holding ports — mop up unconditionally. ProcessLookupError
    # (group already gone) is swallowed by _signal_group.
    if group is not None:
        _signal_group(group, _signal.SIGKILL)


def _stop_children(children: list[subprocess.Popen]) -> None:
    for child in children:
        if child.poll() is None:
            _terminate_tree(child)


def _install_signal_handlers() -> tuple:
    """Route SIGTERM/SIGHUP through the normal exit path.

    Without a disposition, a TERM kills the wrapper instantly and the
    finally-block never runs — the child trees survive orphaned, still
    holding their ports. Raising ``SystemExit(0)`` from the handler
    unwinds through the cleanup like Ctrl+C does.
    """

    def _raise_exit(signum: int, frame: FrameType | None) -> None:  # noqa: ARG001
        raise SystemExit(0)

    signals = [signal.SIGTERM]
    if hasattr(signal, "SIGHUP"):
        signals.append(signal.SIGHUP)
    saved = tuple(signal.getsignal(sig) for sig in signals)
    for sig in signals:
        signal.signal(sig, _raise_exit)
    return saved


def _restore_signal_handlers(saved: tuple) -> None:
    signals = [signal.SIGTERM]
    if hasattr(signal, "SIGHUP"):
        signals.append(signal.SIGHUP)
    for sig, previous in zip(signals, saved, strict=False):
        signal.signal(sig, previous)


# ---------------------------------------------------------------------------
# serve preflight
# ---------------------------------------------------------------------------


_ACK_TRUTHY = {"1", "true", "yes", "on"}


def _env_ack(name: str) -> bool:
    """Read an acknowledge-this-risk flag. Only explicit truthy values ack;
    ``0``/``false``/``off`` (and unset) keep the guard enforced — a user
    writing ``=0`` expects the safeguard, not a bypass."""
    return os.environ.get(name, "").strip().lower() in _ACK_TRUTHY


def _serve_preflight(workers: int) -> None:
    """Fail fast on configurations that boot but break under serve."""
    from fastplace.config import config
    from fastplace.errors import ConfigurationError
    from fastplace.http.session import resolve_session_driver

    # Mirror of the framework's production cache refusal: per-process
    # stores under multi-worker serve flap auth state between workers.
    if (
        workers > 1
        and resolve_session_driver() == "memory"
        and not _env_ack("SESSION_ALLOW_MEMORY_MULTIWORKER")
    ):
        raise ConfigurationError(
            f"serve refuses memory sessions with {workers} workers — each worker "
            "answers from its own in-process store, so logins and CSRF tokens "
            "flap between workers; set SESSION_DRIVER to 'database' or 'redis', "
            "or acknowledge this deployment with SESSION_ALLOW_MEMORY_MULTIWORKER=1"
        )

    env = str(config("APP_ENV", default="local")).lower()
    cache_driver = str(config("CACHE_DRIVER", default="memory")).strip().lower()
    if (
        env == "production"
        and cache_driver == "memory"
        and not _env_ack("CACHE_ALLOW_MEMORY_IN_PRODUCTION")
    ):
        # Same refusal the kernel raises at boot — surfaced here so a fresh
        # scaffold gets a clean CLI error instead of a worker crash loop.
        raise ConfigurationError(
            "production refuses CACHE_DRIVER=memory — per-process counters "
            "under-count rate limits under multi-worker serve; set CACHE_DRIVER "
            "to 'redis' or 'database', or acknowledge a single-worker deployment "
            "with CACHE_ALLOW_MEMORY_IN_PRODUCTION=1"
        )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


@run_app.command("dev")
def run_dev(
    host: str = typer.Option(None, help="Bind host (default: APP_HOST or 127.0.0.1)."),
    port: int = typer.Option(None, help="Bind port (default: APP_PORT or 8000)."),
    skip_vite: bool = typer.Option(False, "--skip-vite", help="Do not start the Vite dev server."),
    skip_lint: bool = typer.Option(
        False, "--skip-lint", help="Do not start the module-boundary lint watcher."
    ),
) -> None:
    """Run the ASGI backend (Uvicorn reload) + Vite dev server (HMR) together."""
    _load_project_env()
    host = host or _cfg("APP_HOST", "127.0.0.1")
    port = port or int(_cfg("APP_PORT", "8000"))
    vite_port = _cfg("VITE_PORT", "5173")
    vite_url = f"http://localhost:{vite_port}"

    from fastplace.http.session import resolve_session_driver

    if resolve_session_driver() == "memory":
        console.print(
            "[warning]memory sessions[/] — the reload server restarts on every "
            "backend save and the in-process store dies with it (every save "
            "will log you out). Set [cyan]SESSION_DRIVER=database[/] in .env to "
            "keep sessions across reloads."
        )

    console.print("[fastplace]Fastplace[/fastplace] development server")
    console.print(f"  backend  → http://{host}:{port}")
    console.print(f"  vite     → {vite_url}  [dim](serves bridge assets in dev)[/]")

    children: list[subprocess.Popen] = []
    # The dev runtime never resolves serve-style assets, whatever the shell
    # happened to export — the Vite dev server owns asset URLs here, and
    # declaring "dev" also keeps PrerenderStaticFiles out of the stack so a
    # stray prerender output can't shadow live pages during development.
    env = {**os.environ, "VITE_DEV_URL": vite_url, "FASTPLACE_RUNTIME": "dev"}

    # Handlers go in BEFORE the first spawn: a TERM landing between spawn
    # and install would kill the wrapper without the cleanup finally-block,
    # orphaning every child tree.
    saved_signals = _install_signal_handlers()
    try:
        # dev_shell re-exports the project app but keeps answering (with a
        # self-refreshing 503) while a broken import would otherwise hang the
        # reloader's socket — see fastplace/http/dev_shell.py.
        backend = _uvicorn_command(
            "fastplace.http.dev_shell:app", "--reload", "--host", host, "--port", str(port)
        )
        children.append(_spawn(backend, cwd=_project_root(), env=env))

        # Instant boundary feedback on save (blueprint §4): a third child
        # re-runs `lint:modules` semantics on every app/**.py edit.
        if not skip_lint:
            fastplace = _fastplace_bin()
            if fastplace:
                console.print("  lint     → module boundaries re-checked on save")
                children.append(_spawn([fastplace, "lint:watch"], cwd=_project_root(), env=env))
            else:
                console.print("[warning]fastplace CLI not found — skipping lint watcher.[/]")

        if not skip_vite and (Path.cwd() / "package.json").exists():
            npm = shutil.which("npm")
            if npm:
                children.append(
                    _spawn(
                        [npm, "run", "dev"],
                        cwd=_project_root(),
                        env={**env, "PORT": vite_port},
                    )
                )
            else:
                console.print("[warning]npm not found — skipping Vite dev server.[/]")

        children[0].wait()
    except KeyboardInterrupt:
        pass
    finally:
        _stop_children(children)
        _restore_signal_handlers(saved_signals)


@serve_app.command("serve")
def serve(
    host: str = typer.Option(None, help="Bind host (default: APP_HOST or 0.0.0.0)."),
    port: int = typer.Option(None, help="Bind port (default: APP_PORT or 8000)."),
    workers: int = typer.Option(None, help="Worker count (default: APP_WORKERS or CPU count)."),
    skip_build: bool = typer.Option(False, "--skip-build", help="Skip the Vite production build."),
    forwarded_allow_ips: str = typer.Option(
        None,
        "--forwarded-allow-ips",
        help="Trust X-Forwarded-* only from these IPs/CIDRs (uvicorn passthrough).",
    ),
    no_proxy_headers: bool = typer.Option(
        False,
        "--no-proxy-headers",
        help="Ignore X-Forwarded-* headers entirely (direct-TLS deployments).",
    ),
) -> None:
    """Build frontend assets, then launch the optimized multi-worker ASGI server."""
    _load_project_env()

    host = host or _cfg("APP_HOST", "0.0.0.0")
    port = port or int(_cfg("APP_PORT", "8000"))
    workers = workers or int(_cfg("APP_WORKERS", str(max(1, (os.cpu_count() or 2) - 1))))

    from fastplace.errors import ConfigurationError

    try:
        _serve_preflight(workers)
    except ConfigurationError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if not skip_build and (Path.cwd() / "package.json").exists():
        npm = shutil.which("npm")
        if npm:
            console.print("[info]Building frontend assets…[/]")
            result = subprocess.run([npm, "run", "build"], cwd=_project_root())
            if result.returncode != 0:
                console.print("[error]Frontend build failed.[/]")
                raise typer.Exit(code=1)

    console.print(
        f"[fastplace]Fastplace[/fastplace] serving http://{host}:{port} ({workers} workers)"
    )
    command = _uvicorn_command(
        "asgi:app", "--host", host, "--port", str(port), "--workers", str(workers)
    )
    if forwarded_allow_ips:
        command += ["--forwarded-allow-ips", forwarded_allow_ips]
    if no_proxy_headers:
        command += ["--no-proxy-headers"]

    # The serve runtime marker: asset resolution keys on this, not APP_ENV,
    # so a local-env serve never emits dead localhost:5173 dev tags.
    env = {**os.environ, "FASTPLACE_RUNTIME": "serve"}

    # Handlers go in BEFORE the spawn — same orphaning window as run dev.
    saved_signals = _install_signal_handlers()
    child = _spawn(command, cwd=_project_root(), env=env)
    exit_code = 0
    try:
        child.wait()
        exit_code = child.returncode or 0
    except KeyboardInterrupt:
        _quiet_exit()
    finally:
        if child.poll() is None:
            _terminate_tree(child)
        _restore_signal_handlers(saved_signals)
    # A supervised stop should read as success; any other child exit —
    # crash, bind failure, bad config — propagates to the caller (systemd,
    # deploy script) instead of always looking like 0.
    if exit_code:
        raise typer.Exit(code=exit_code)


def _quiet_exit() -> None:
    # Uvicorn handles SIGINT; swallow the interpreter-level traceback.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
