"""`fastplace run dev` and `fastplace serve` — server runtimes."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import typer

from fastplace.console import console

run_app = typer.Typer(help="Server runtimes.")
serve_app = typer.Typer(help="Production serving.")


def _project_root() -> Path:
    return Path(os.getcwd())


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
    host = host or _cfg("APP_HOST", "127.0.0.1")
    port = port or int(_cfg("APP_PORT", "8000"))
    vite_port = _cfg("VITE_PORT", "5173")
    vite_url = f"http://localhost:{vite_port}"

    console.print("[fastplace]Fastplace[/fastplace] development server")
    console.print(f"  backend  → http://{host}:{port}")
    console.print(f"  vite     → {vite_url}  [dim](serves bridge assets in dev)[/]")

    children: list[subprocess.Popen] = []
    env = {**os.environ, "VITE_DEV_URL": vite_url}

    backend = _uvicorn_command("asgi:app", "--reload", "--host", host, "--port", str(port))
    children.append(subprocess.Popen(backend, cwd=_project_root(), env=env))

    # Instant boundary feedback on save (blueprint §4): a third child
    # re-runs `lint:modules` semantics on every app/**.py edit.
    if not skip_lint:
        fastplace = _fastplace_bin()
        if fastplace:
            console.print("  lint     → module boundaries re-checked on save")
            children.append(
                subprocess.Popen([fastplace, "lint:watch"], cwd=_project_root(), env=env)
            )
        else:
            console.print("[warning]fastplace CLI not found — skipping lint watcher.[/]")

    if not skip_vite and (Path.cwd() / "package.json").exists():
        npm = shutil.which("npm")
        if npm:
            children.append(
                subprocess.Popen(
                    [npm, "run", "dev"],
                    cwd=_project_root(),
                    env={**env, "PORT": vite_port},
                )
            )
        else:
            console.print("[warning]npm not found — skipping Vite dev server.[/]")

    try:
        children[0].wait()
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()


@serve_app.command("serve")
def serve(
    host: str = typer.Option(None, help="Bind host (default: APP_HOST or 0.0.0.0)."),
    port: int = typer.Option(None, help="Bind port (default: APP_PORT or 8000)."),
    workers: int = typer.Option(None, help="Worker count (default: APP_WORKERS or CPU count)."),
    skip_build: bool = typer.Option(False, "--skip-build", help="Skip the Vite production build."),
) -> None:
    """Build frontend assets, then launch the optimized multi-worker ASGI server."""
    if not skip_build and (Path.cwd() / "package.json").exists():
        npm = shutil.which("npm")
        if npm:
            console.print("[info]Building frontend assets…[/]")
            result = subprocess.run([npm, "run", "build"], cwd=_project_root())
            if result.returncode != 0:
                console.print("[error]Frontend build failed.[/]")
                raise typer.Exit(code=1)

    host = host or _cfg("APP_HOST", "0.0.0.0")
    port = port or int(_cfg("APP_PORT", "8000"))
    workers = workers or int(_cfg("APP_WORKERS", str(max(1, (os.cpu_count() or 2) - 1))))

    console.print(
        f"[fastplace]Fastplace[/fastplace] serving http://{host}:{port} ({workers} workers)"
    )
    command = _uvicorn_command(
        "asgi:app", "--host", host, "--port", str(port), "--workers", str(workers)
    )
    try:
        subprocess.run(command, cwd=_project_root())
    except KeyboardInterrupt:
        _quiet_exit()


def _quiet_exit() -> None:
    # Uvicorn handles SIGINT; swallow the interpreter-level traceback.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
