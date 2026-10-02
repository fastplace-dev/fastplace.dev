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
from pathlib import Path
from types import FrameType, ModuleType
from typing import Any, overload

import typer

from fastplace.cli._interp import project_fastplace_bin, project_python
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


def _uvicorn_command(root: Path, *args: str) -> list[str]:
    """The backend imports the project's app, so it runs on the project's
    env — a global fastplace install must not serve an app through its own
    (dependency-free) interpreter. Falls back to the running interpreter
    when the project has no .venv."""
    return [project_python(root), "-m", "uvicorn", *args]


# ---------------------------------------------------------------------------
# Bind-port resolution
# ---------------------------------------------------------------------------

#: The bind port used when neither ``--port`` nor APP_PORT names one.
DEFAULT_PORT = 9000

#: How many consecutive ports the auto-fallback walks before giving up.
_PORT_FALLBACK_ATTEMPTS = 10


def _port_is_free(host: str, port: int) -> bool:
    """True unless a live listener already holds ``(host, port)``.

    Bind-only probe with ``SO_REUSEADDR`` — the same posture uvicorn binds
    with — so the probe agrees with the server on TIME_WAIT sockets while a
    live listener still reports busy. Only EADDRINUSE counts as busy: bind
    errors about the host itself (EADDRNOTAVAIL, EACCES, ...) are left for
    the server to surface, so the probe never invents a conflict uvicorn
    would not hit. Parity is best-effort — a listener on another address
    family, or SO_REUSEADDR double-binds on Windows, can read as free — so
    the server's own bind stays authoritative for races the probe cannot
    see, and its exit code propagates to the wrapper.
    """
    import errno
    import socket

    busy = {errno.EADDRINUSE}
    if hasattr(errno, "WSAEADDRINUSE"):  # pragma: no cover — Windows
        busy.add(errno.WSAEADDRINUSE)
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OverflowError:
        # Out-of-range port (not an OSError): nothing can ever bind it.
        return False
    except OSError as exc:
        return exc.errno not in busy
    finally:
        sock.close()
    return True


def _first_free_port(host: str, start: int, attempts: int) -> int | None:
    for candidate in range(start, min(start + attempts, 65536)):
        if _port_is_free(host, candidate):
            return candidate
    return None


def _resolve_bind_port(host: str, preferred: int, *, fallback: bool) -> int:
    """Pick the port the server will actually bind.

    Strict mode (an explicit ``--port``/APP_PORT, and every ``serve`` bind)
    treats a busy port as an error naming the nearest free alternative.
    Fallback mode — only for the dev server's built-in default — walks
    9000, 9001, ... so a second dev server simply moves up one slot.
    """
    from fastplace.errors import ConfigurationError

    if not 1 <= preferred <= 65535:
        raise ConfigurationError(f"port must be between 1 and 65535, got {preferred}")
    if _port_is_free(host, preferred):
        return preferred
    if not fallback:
        nearest = _first_free_port(host, preferred + 1, _PORT_FALLBACK_ATTEMPTS)
        hint = f" — nearest free port: {nearest} (pass --port {nearest})" if nearest else ""
        raise ConfigurationError(f"port {preferred} on {host} is already in use{hint}")
    shifted = _first_free_port(host, preferred + 1, _PORT_FALLBACK_ATTEMPTS - 1)
    if shifted is None:
        last = min(preferred + _PORT_FALLBACK_ATTEMPTS - 1, 65535)
        raise ConfigurationError(
            f"no free port on {host} from {preferred} through {last} — pass --port to name one"
        )
    return shifted


@overload
def _cfg_int(key: str) -> int | None: ...


@overload
def _cfg_int(key: str, default: int) -> int: ...


def _cfg_int(key: str, default: int | None = None) -> int | None:
    """An integer-valued config entry, ``default`` when unset; malformed
    values are a clean ConfigurationError naming the key."""
    from fastplace.config import config
    from fastplace.errors import ConfigurationError

    raw = config(key, None)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        raise ConfigurationError(f"{key} must be an integer, got {raw!r}") from None


def _cfg_port() -> int | None:
    """APP_PORT validated to the bind range, None when unset."""
    from fastplace.errors import ConfigurationError

    port = _cfg_int("APP_PORT")
    if port is not None and not 1 <= port <= 65535:
        raise ConfigurationError(f"APP_PORT must be between 1 and 65535, got {port}")
    return port


def _require_min(name: str, value: int, *, minimum: int = 1) -> None:
    """Shared floor check for the CLI family's integer options and env knobs.

    Zero is a real value, not "unset": `--workers 0` and `--steps 0` name
    requests the command cannot honor (a zero-worker server, a zero-step
    revert) and used to be swallowed by `or`-style defaults or clamped
    downstream — reject them here with the value named. Options where 0 is
    a valid value never route through this check.
    """
    from fastplace.errors import ConfigurationError

    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}, got {value}")


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
# Prerender tree vs. the Vite build
# ---------------------------------------------------------------------------


def _prerender_manifest_path(root: Path) -> Path:
    return root / "public" / "build" / "prerender" / "prerender-manifest.json"


def _recapture_prerender(root: Path, *, had_tree: bool) -> bool:
    """Rebuild the prerender tree a `npm run build` just wiped.

    The scaffold's Vite config empties public/build wholesale
    (``emptyOutDir``), taking ``public/build/prerender`` with it. An app
    that ships prerendered — a pre-build manifest — or configures routes
    explicitly (``PRERENDER_ROUTES`` on the asgi module, or the env var
    of the same name) gets the tree recaptured in-process through the
    same ``fastplace.prerender`` path the CLI command uses. A
    never-prerendered app with no explicit routes is left alone.

    Returns True when a capture ran; any failure raises to the caller.
    """
    import asyncio
    import os

    from fastplace.cli.prerender_cmd import _load_app
    from fastplace.prerender.capture import capture_all
    from fastplace.prerender.routes import resolve_prerender_routes
    from fastplace.prerender.writer import write_pages

    loaded: tuple[Any, ModuleType] | None = None
    if not had_tree and not os.environ.get("PRERENDER_ROUTES", "").strip():
        # No tree, no env routes: prerender only if the asgi module opted
        # in. An import failure here reads as "no explicit routes" — the
        # uvicorn child reports the real boot error, serve must not
        # pre-empt it with a wrapper-time one.
        try:
            loaded = _load_app(root, None)
        except Exception:  # noqa: BLE001 — boot diagnostics belong to uvicorn
            return False
        if not hasattr(loaded[1], "PRERENDER_ROUTES"):
            return False

    app, app_module = loaded if loaded is not None else _load_app(root, None)
    routes = resolve_prerender_routes(app_module, [], root=root)
    if not routes:
        return False
    pages = asyncio.run(capture_all(app, routes))
    out_dir = root / "public" / "build" / "prerender"
    manifest = write_pages(pages, out_dir)
    if manifest.routes:
        manifest.write(out_dir)
    return True


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


@run_app.command("dev")
def run_dev(
    host: str = typer.Option(None, help="Bind host (default: APP_HOST or 127.0.0.1)."),
    port: int = typer.Option(
        None,
        help="Bind port (default: APP_PORT or 9000; the default auto-falls back to the next free port).",
    ),
    skip_vite: bool = typer.Option(False, "--skip-vite", help="Do not start the Vite dev server."),
    skip_lint: bool = typer.Option(
        False, "--skip-lint", help="Do not start the module-boundary lint watcher."
    ),
) -> None:
    """Run the ASGI backend (Uvicorn reload) + Vite dev server (HMR) together."""
    _load_project_env()
    host = host or _cfg("APP_HOST", "127.0.0.1")

    from fastplace.errors import ConfigurationError

    try:
        # The CLI flag wins before .env is even parsed — an explicit --port
        # is how a user escapes a broken APP_PORT, not something it vetoes.
        configured = None if port is not None else _cfg_port()
        requested = (
            port if port is not None else (DEFAULT_PORT if configured is None else configured)
        )
        explicit_port = port is not None or configured is not None
        port = _resolve_bind_port(host, requested, fallback=not explicit_port)
    except ConfigurationError as exc:
        console.print(str(exc), style="red", markup=False)
        raise typer.Exit(code=1) from exc

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
    if port != requested:
        # markup=False: host comes from APP_HOST — interpolated values are
        # data, never Rich tags.
        console.print(
            f"  port {requested} on {host} is busy — using {port}",
            style="warning",
            markup=False,
        )
        console.print(
            "  APP_URL-derived URLs (passkey origins, signed links) still honor "
            "APP_URL, not the shifted port",
            style="warning",
        )
    console.print(f"  vite     → {vite_url}  [dim](serves bridge assets in dev)[/]")

    children: list[subprocess.Popen] = []
    # A backend that dies on its own (bad bind from a lost race, import
    # crash under reload) must fail the wrapper — supervisors key on this
    # exit code, not the child's.
    exit_code = 0
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
            _project_root(),
            "fastplace.http.dev_shell:app",
            "--reload",
            "--host",
            host,
            "--port",
            str(port),
        )
        children.append(_spawn(backend, cwd=_project_root(), env=env))

        # Instant boundary feedback on save (blueprint §4): a third child
        # re-runs `lint:modules` semantics on every app/**.py edit.
        if not skip_lint:
            fastplace = project_fastplace_bin(_project_root())
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

        exit_code = children[0].wait()
    except KeyboardInterrupt:
        pass
    finally:
        _stop_children(children)
        _restore_signal_handlers(saved_signals)
    if exit_code:
        # wait() reports a signal death negative; shells encode it 128+N —
        # meet supervisors on the convention they actually key on.
        raise typer.Exit(code=128 - exit_code if exit_code < 0 else exit_code)


@serve_app.command("serve")
def serve(
    host: str = typer.Option(None, help="Bind host (default: APP_HOST or 0.0.0.0)."),
    port: int = typer.Option(None, help="Bind port (default: APP_PORT or 9000)."),
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

    from fastplace.errors import ConfigurationError

    try:
        # Same precedence as run dev: the CLI flag wins before .env is
        # parsed, so an explicit --port escapes a broken APP_PORT. The
        # workers parse lives here too — a malformed APP_WORKERS gets the
        # same clean one-line error instead of a traceback. An explicit
        # `--workers 0`/`APP_WORKERS=0` is rejected (is None, not `or`):
        # 0 is falsy and used to silently become the CPU-count default.
        configured = None if port is not None else _cfg_port()
        port = port if port is not None else (DEFAULT_PORT if configured is None else configured)
        if workers is None:
            workers = _cfg_int("APP_WORKERS", max(1, (os.cpu_count() or 2) - 1))
        _require_min("workers", workers)
        _serve_preflight(workers)
        # Production binds never auto-fallback: a server that silently moves
        # one port up breaks every reverse proxy aimed at the expected one.
        # The probe is advisory — a race can still lose the port mid-build —
        # so uvicorn's own bind stays authoritative and its exit code
        # propagates below.
        port = _resolve_bind_port(host, port, fallback=False)
    except ConfigurationError as exc:
        console.print(str(exc), style="red", markup=False)
        raise typer.Exit(code=1) from exc

    if not skip_build and (Path.cwd() / "package.json").exists():
        npm = shutil.which("npm")
        if npm:
            # Snapshot before the build: Vite's emptyOutDir wipes
            # public/build wholesale, taking the prerender tree with it.
            had_prerender_tree = _prerender_manifest_path(_project_root()).is_file()
            console.print("[info]Building frontend assets…[/]")
            result = subprocess.run([npm, "run", "build"], cwd=_project_root())
            if result.returncode != 0:
                console.print("[error]Frontend build failed.[/]")
                raise typer.Exit(code=1)
            try:
                if _recapture_prerender(_project_root(), had_tree=had_prerender_tree):
                    console.print("[green]prerender tree rebuilt[/] after the Vite build")
            except Exception as exc:  # noqa: BLE001 — the tree is part of the deploy
                # markup=False: exception text is data, never Rich tags.
                console.print(f"prerender recapture failed: {exc}", style="red", markup=False)
                raise typer.Exit(code=1) from exc

    console.print(
        f"[fastplace]Fastplace[/fastplace] serving http://{host}:{port} ({workers} workers)"
    )
    command = _uvicorn_command(
        _project_root(), "asgi:app", "--host", host, "--port", str(port), "--workers", str(workers)
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
    # deploy script) instead of always looking like 0. Signal deaths are
    # normalized to the shell's 128+N convention, as in run dev.
    if exit_code:
        raise typer.Exit(code=128 - exit_code if exit_code < 0 else exit_code)


def _quiet_exit() -> None:
    # Uvicorn handles SIGINT; swallow the interpreter-level traceback.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
