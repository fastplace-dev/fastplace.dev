"""DevX/testing commands: matrix, db, coverage, watch, e2e, doctor."""

from __future__ import annotations

import subprocess
import sys
import time
import uuid
from pathlib import Path

import typer

from fastplace.console import console

testing_app = typer.Typer(help="Testing workflows and environment diagnostics.")

#: Module seams — tests stub these; no real docker/npx/pytest child ever runs in tests.
_subprocess_run = subprocess.run
_sleep = time.sleep


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


_BACKENDS: dict[str, dict] = {
    "postgres": {
        "image": "pgvector/pgvector:pg16",
        "container_port": 5432,
        "env": {
            "POSTGRES_USER": "fastplace",
            "POSTGRES_PASSWORD": "fastplace",
            "POSTGRES_DB": "fastplace_test",
        },
        "health": ["pg_isready", "-U", "fastplace"],
        "env_var": "TEST_POSTGRES_URL",
        "url_template": "postgresql+asyncpg://fastplace:fastplace@{host}:{port}/fastplace_test",
        "dialect_dir": "tests/orm/postgresql",
        "driver": "asyncpg",
        "extra": "postgresql",
        "vector_extension": True,
    },
    "mysql": {
        "image": "mysql:8",
        "container_port": 3306,
        "env": {
            "MYSQL_ROOT_PASSWORD": "root",
            "MYSQL_DATABASE": "fastplace_test",
            "MYSQL_USER": "fastplace",
            "MYSQL_PASSWORD": "fastplace",
        },
        "health": ["mysqladmin", "ping", "-h", "127.0.0.1", "-uroot", "-proot", "--silent"],
        "env_var": "TEST_MYSQL_URL",
        "url_template": "mysql+asyncmy://fastplace:fastplace@{host}:{port}/fastplace_test",
        "dialect_dir": "tests/orm/mysql",
        "driver": "asyncmy",
        "extra": "mysql",
        "vector_extension": False,
    },
    "mongodb": {
        "image": "mongo:7",
        "container_port": 27017,
        "env": {},
        "health": [
            "mongosh",
            "--quiet",
            "--eval",
            "db.runCommand({ ping: 1 }).ok",
            "localhost:27017/test",
        ],
        "env_var": "TEST_MONGODB_URL",
        "url_template": "mongodb://{host}:{port}/fastplace_test",
        "dialect_dir": "tests/orm/mongodb",
        "driver": "pymongo",
        "extra": "mongodb",
        "vector_extension": False,
    },
}


def _driver_available(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


async def _ensure_pgvector(url: str) -> None:
    """CREATE EXTENSION IF NOT EXISTS vector, or dialect VECTOR tests fail (ci.yml:123-138)."""
    import asyncpg

    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await conn.close()


def _mapped_host_port(container: str, port: int) -> tuple[str, int]:
    out = _subprocess_run(
        ["docker", "port", container, str(port)], capture_output=True, text=True
    ).stdout
    for line in out.splitlines():
        if line.startswith("127.0.0.1:"):
            host, _, mapped = line.rpartition(":")
            return host, int(mapped)
    raise RuntimeError(f"no 127.0.0.1 mapping found for container port {port}")


def _wait_healthy(container: str, health: list[str], backend: str) -> None:
    for _ in range(30):  # ~60s total; mysql:8 needs the 10-30s patience CI gives it
        probe = _subprocess_run(["docker", "exec", container, *health], capture_output=True)
        if probe.returncode == 0:
            return
        _sleep(2)
    raise RuntimeError(f"{backend} container never became healthy")


def _run_backend(root: Path, backend: str, spec: dict, keep: bool, extra_args: list[str]) -> bool:
    """One backend lifecycle (existing-stack reuse or disposable container)."""
    import asyncio
    import os

    url = os.environ.get(spec["env_var"])
    cid: str | None = None
    try:
        if url:
            console.print(f"[cyan]{backend}[/] using exported {spec['env_var']} (existing stack)")
        else:
            if not _driver_available(spec["driver"]):
                console.print(
                    f"[red]{backend} driver '{spec['driver']}' not installed[/] — "
                    f"pip install -e '.[{spec['extra']}]'\n"
                    "(the portable fixtures have no import guard — they error noisily, not skip)"
                )
                return False
            name = f"fastplace-matrix-{backend}-{uuid.uuid4().hex[:8]}"
            run_argv = [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "-p",
                f"127.0.0.1::{spec['container_port']}",
            ]
            for key, value in spec["env"].items():
                run_argv += ["-e", f"{key}={value}"]
            run_argv.append(spec["image"])
            cid = _subprocess_run(run_argv, capture_output=True, text=True).stdout.strip()
            try:
                _wait_healthy(cid, spec["health"], backend)
            except RuntimeError as exc:
                console.print(f"[red]{exc}[/]")
                return False
            host, port = _mapped_host_port(cid, spec["container_port"])
            url = spec["url_template"].format(host=host, port=port)
            if spec["vector_extension"]:
                asyncio.run(_ensure_pgvector(url))
            console.print(f"[cyan]{backend}[/] container up: {url}")

        suite = _subprocess_run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/orm/portable",
                spec["dialect_dir"],
                *extra_args,
            ],
            cwd=root,
            env={**os.environ, spec["env_var"]: url},
        )
        return suite.returncode == 0
    except Exception as exc:  # noqa: BLE001 — any failure path still reports and tears down
        console.print(f"[red]{backend} failed: {exc}[/]")
        return False
    finally:
        # The finally covers every escape path — unexpected errors (port-map
        # parse, pgvector connect refused) and Ctrl-C (KeyboardInterrupt
        # propagates after this runs) — so no fastplace-matrix-* container
        # is ever orphaned; --keep deliberately opts out.
        if cid and keep:
            console.print(f"[dim]{backend} container kept: {name} ({url})[/]")
        elif cid:
            _subprocess_run(["docker", "rm", "-f", cid], capture_output=True)


@testing_app.command("test:matrix")
def test_matrix(
    backends: str = typer.Option(
        "",
        "--backends",
        help=(
            "Comma-separated subset of postgres,mysql,mongodb. Default: backends "
            "with exported TEST_*_URLs, else all three."
        ),
    ),
    keep: bool = typer.Option(
        False, "--keep", help="Keep containers alive after the run (prints name + URL)."
    ),
    extra_args: list[str] | None = typer.Argument(None, help="Forwarded to pytest verbatim."),
) -> None:
    """Portable ORM suite (+ dialect dir) against disposable containers — never the full suite per backend."""
    import os

    root = _project_root()
    if backends:
        chosen = [b.strip() for b in backends.split(",") if b.strip()]
        if not chosen:  # separators-only value like "," would silently run nothing
            console.print(
                f'[red]no backends in "--backends"[/] — choose from {", ".join(_BACKENDS)}'
            )
            raise typer.Exit(code=1)
    else:
        chosen = [b for b, spec in _BACKENDS.items() if os.environ.get(spec["env_var"])] or list(
            _BACKENDS
        )
    unknown = [b for b in chosen if b not in _BACKENDS]
    if unknown:
        console.print(
            f"[red]unknown backends: {', '.join(unknown)}[/] — choose from {', '.join(_BACKENDS)}"
        )
        raise typer.Exit(code=1)

    if any(not os.environ.get(_BACKENDS[b]["env_var"]) for b in chosen):
        probe = _subprocess_run(["docker", "--version"], capture_output=True)
        if probe.returncode != 0:
            console.print(
                "[red]docker not on PATH[/] — install Docker, or export TEST_*_URLs "
                "to point test:matrix at an already-running stack."
            )
            raise typer.Exit(code=1)

    failures = [
        b for b in chosen if not _run_backend(root, b, _BACKENDS[b], keep, list(extra_args or []))
    ]
    if failures:
        console.print(f"[red]matrix failed: {', '.join(failures)}[/]")
        raise typer.Exit(code=1)
    console.print("[green]matrix passed.[/]")


_SCRATCH_DB_NAME = "testing.sqlite3"


def _run_migrations(root: Path) -> bool:
    """Real migrations; False = not configured (friendly skip, not an error)."""
    from fastplace.orm.migrations import MigrationsManager

    manager = MigrationsManager(root)
    if not manager.configured:
        # Same guard as `db migrate` (fastplace/cli/database.py), but a test
        # scratch database without migrations is expected, not a refusal.
        console.print(
            "[yellow]Migrations are not configured — skipping migrations[/] "
            "([cyan]fastplace db:configure[/] scaffolds them)."
        )
        return False
    manager.upgrade()
    return True


def _run_seeders(root: Path) -> None:
    from fastplace.orm.migrations import run_seeders

    run_seeders(root)


def _masked_url(url: str) -> str:
    """URL with any password replaced by *** (db:cli precedent)."""
    import re as _re

    return _re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", url)


@testing_app.command("test:db")
def test_db(
    seed: bool = typer.Option(False, "--seed", help="Run database seeders after migrating."),
    database_url: str = typer.Option(
        "",
        "--database-url",
        help="Non-sqlite scratch target. Masked in output; production + non-local asks to confirm.",
    ),
    force: bool = typer.Option(False, "--force", help="Skip the production confirmation."),
) -> None:
    """Disposable scratch database: sqlite by default, your configured DB never touched."""
    import os

    from fastplace.config import config, load_env, reset_config
    from fastplace.db import reset_db

    root = _project_root()
    load_env(root / ".env")  # FIRST — the scratch override must beat .env (override=False)
    reset_config(root)

    if database_url:
        scratch_url = database_url
        local_scratch = scratch_url.startswith("sqlite")
    else:
        storage = root / "storage"
        storage.mkdir(parents=True, exist_ok=True)  # fresh worktrees lack it
        scratch_url = f"sqlite+aiosqlite:///{storage / _SCRATCH_DB_NAME}"
        local_scratch = True

    if (
        str(config("APP_ENV", default="production")).lower() == "production"
        and not local_scratch
        and not (force or typer.confirm("Point tests at this non-local database in production?"))
    ):
        console.print("[red]aborted[/]")
        raise typer.Exit(code=1)

    os.environ["DATABASE_URL"] = scratch_url
    if local_scratch:
        os.environ["DATABASE_DRIVER"] = "sqlite"

    if not database_url:
        # Teardown belongs to the default scratch path only: a user-supplied
        # --database-url (sqlite or not) names its own target, which we never
        # delete — and the default storage triple must survive untouched.
        db_path = storage / _SCRATCH_DB_NAME
        for stale in (
            db_path,
            db_path.with_name(db_path.name + "-wal"),
            db_path.with_name(db_path.name + "-shm"),
        ):
            stale.unlink(missing_ok=True)
        console.print(f"[dim]scratch database: {db_path}[/]")
    else:
        # Never print a raw DB URL: credentials stay out of terminals and CI logs.
        console.print(f"[dim]scratch database: {_masked_url(scratch_url)}[/]")

    reset_db()  # fresh manager picks up the scratch URL
    ran = _run_migrations(root)
    if seed:
        try:
            _run_seeders(root)
        except ValueError as exc:
            console.print(f"[red]seeder failed: {exc}[/]")
            raise typer.Exit(code=1) from exc
    if ran:
        console.print(
            "[green]scratch database ready.[/] [dim]never touched your configured database[/]"
        )
    else:
        console.print(
            "[green]scratch database ready (no migrations configured).[/] "
            "[dim]never touched your configured database[/]"
        )


def _render_coverage_report(json_path: Path, min_percent: float | None) -> int:
    """Parse coverage JSON, render the 15 least-covered modules, gate on --min."""
    import json

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        console.print(
            f"[red]no parsable {json_path.name}[/] — did the pytest run produce "
            "--cov-report=json output? (uv sync --extra dev installs pytest-cov)"
        )
        return 1

    from rich.table import Table

    rows = sorted(
        (name, mod.get("summary", {}).get("percent_covered"))
        for name, mod in data.get("files", {}).items()
    )
    table = Table(title="Least-covered modules")
    table.add_column("MODULE")
    table.add_column("%", justify="right")
    shown = 0
    for name, pct in sorted(rows, key=lambda r: (r[1] is None, r[1] or 0)):
        if shown >= 15:
            break
        shown += 1
        table.add_row(name, "—" if pct is None else f"{pct:.1f}")
    console.print(table)

    total = data.get("totals", {}).get("percent_covered")
    if total is None:
        console.print("[red]totals.percent_covered missing from coverage JSON[/]")
        return 1
    console.print(f"total: [bold]{total:.1f}%[/] covered")
    if min_percent is not None and total < min_percent:
        console.print(
            f"[red]coverage {total:.1f}% below --min {min_percent:g}% "
            f"(shortfall {min_percent - total:.1f})[/]"
        )
        return 1
    return 0


@testing_app.command("test:coverage")
def test_coverage(
    min_percent: float | None = typer.Option(
        None,
        "--min",
        help="Exit 1 when totals.percent_covered falls below this. No default: no measured baseline exists yet.",
    ),
    extra_args: list[str] | None = typer.Argument(
        None, help="Forwarded to pytest verbatim (e.g. tests/cli)."
    ),
) -> None:
    """Pytest with coverage, then a least-covered report and optional --min gate."""
    root = _project_root()
    json_path = root / "storage" / "coverage.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    proc = _subprocess_run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--cov=fastplace",
            "--cov-report=term-missing",
            f"--cov-report=json:{json_path}",
            *(extra_args or []),
        ],
        cwd=root,
    )
    if proc.returncode != 0:
        raise typer.Exit(code=proc.returncode)
    raise typer.Exit(code=_render_coverage_report(json_path, min_percent))


def _relevant_change(path: str) -> bool:
    """A .py under tests/ app/ fastplace/ routes/ — the trees whose saves
    can change test outcomes. e2e/ and node_modules/ are excluded."""
    from pathlib import PurePath

    parts = PurePath(path).parts
    return path.endswith(".py") and any(
        top in parts for top in ("tests", "app", "fastplace", "routes")
    )


def _watch_dirs(root: Path) -> list[Path]:
    """Existing watch roots only — never the repo root (watchfiles would
    take thousands of handles through .venv/node_modules/.git)."""
    return [
        d for d in (root / "tests", root / "app", root / "fastplace", root / "routes") if d.is_dir()
    ]


def _suite_for(path: Path) -> list[str]:
    """Changed test file → that file; changed source → whole suite with -x
    (conservative: a source save is boundary-relevant everywhere)."""
    name = path.name
    if name.startswith("test_") and name.endswith(".py") and "tests" in path.parts:
        return [str(path)]
    return ["-x"]


def _run_suite(extra: list[str]) -> int:
    import sys

    proc = _subprocess_run([sys.executable, "-m", "pytest", "-q", *extra])
    return proc.returncode


@testing_app.command("test:watch")
def test_watch(
    once: bool = typer.Option(
        False, "--once", help="Run a single pass now and exit (no watching)."
    ),
) -> None:
    """Re-run the affected tests on every .py save under tests/ app/ fastplace/ routes/.

    Changed test file → pytest that file; changed source → the whole suite
    with -x. Ctrl-C stops the watcher.
    """
    from fastplace.config import load_env, reset_config

    root = _project_root()
    load_env(root / ".env")
    reset_config(root)

    def _pass() -> int:
        console.print("[dim]running full suite (-x)…[/]")
        return _run_suite(["-x"])

    if once:
        raise typer.Exit(code=_pass())

    dirs = _watch_dirs(root)
    if not dirs:
        console.print(
            "[red]nothing to watch[/] — no tests/ app/ fastplace/ routes/ directories exist."
        )
        raise typer.Exit(code=1)
    console.print(f"watching {', '.join(str(d) for d in dirs)} — Ctrl-C to stop.")
    try:
        from watchfiles import watch

        for changes in watch(*dirs, watch_filter=lambda _change, path: _relevant_change(str(path))):
            for _kind, path in changes:
                target = Path(path)
                if _relevant_change(str(target)):
                    console.print(f"[bold]changed:[/] {target}")
                    code = _run_suite(_suite_for(target))
                    console.print(f"[dim]exit {code}[/]")
    except KeyboardInterrupt:
        pass


def _port_free(port: int) -> bool:
    """Can we bind 127.0.0.1:port? Playwright's webServer sets
    reuseExistingServer: false, so a stale holder breaks its boot."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def _chromium_missing(dry_run_stdout: str) -> bool:
    """True when the chromium line of `playwright install --dry-run`
    reports a pending download. Best-effort parse — the exact table
    format moves between Playwright versions."""
    for line in dry_run_stdout.splitlines():
        if "chromium" in line and "download" in line.lower():
            return True
    return False


@testing_app.command(
    "test:e2e",
    # Forwarded args may be dash-form Playwright flags (--grep, --workers=1):
    # let the parser pass unknown options through to the variadic argument.
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def test_e2e(
    args: list[str] | None = typer.Argument(
        None, help="Args forwarded verbatim to npx playwright test."
    ),
) -> None:
    """Run the Playwright e2e suite with an actionable preflight (browsers, port, storage)."""
    import os
    import shutil
    import subprocess

    from fastplace.config import load_env, reset_config

    root = _project_root()
    load_env(root / ".env")
    reset_config(root)

    npx = shutil.which("npx")
    node = shutil.which("node")
    if not npx or not node:
        missing = "npx" if not npx else "node"
        console.print(
            f"[red]{missing} not on PATH[/] — install Node.js (https://nodejs.org) and retry."
        )
        raise typer.Exit(code=1)

    port = int(os.environ.get("E2E_PORT", "8907"))
    if not _port_free(port):
        console.print(
            f"[red]port 127.0.0.1:{port} is busy[/] — Playwright boots its own server there "
            f"(reuseExistingServer is false). [bold]Set E2E_PORT=<free port>[/] and retry."
        )
        raise typer.Exit(code=1)

    if not (root / "package.json").is_file():
        console.print(
            "[red]no package.json at the project root[/] — this project has no e2e frontend to run."
        )
        raise typer.Exit(code=1)
    if not (root / "node_modules").is_dir():
        console.print("[red]node_modules missing[/] — run [bold]npm ci[/] first.")
        raise typer.Exit(code=1)

    (root / "storage").mkdir(parents=True, exist_ok=True)  # sqlite needs the parent dir

    dry = _subprocess_run(
        [npx, "playwright", "install", "--dry-run"], stdout=subprocess.PIPE, text=True
    )
    if _chromium_missing(dry.stdout or ""):
        console.print(
            "[yellow]chromium browser not installed[/] — run [bold]npx playwright install chromium[/] "
            "(--with-deps is Linux-only; macOS needs the plain form). Continuing anyway…"
        )

    venv_bin = root / ".venv" / "bin" / "fastplace"
    resolved = str(venv_bin) if venv_bin.exists() else "fastplace (PATH)"
    console.print(f"[dim]webServer will resolve: {resolved}[/]")

    proc = _subprocess_run(["npx", "playwright", "test", *(args or [])])
    raise typer.Exit(code=proc.returncode)


def _probe_import(name: str) -> str | None:
    """None when importable; the ImportError text otherwise."""
    import importlib

    try:
        importlib.import_module(name)
    except ImportError as exc:
        return str(exc)
    return None


def _probe_write(path: Path) -> bool:
    """mkdir -p + probe-write + unlink; False when the OS refuses."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".fastplace-write-probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


_DEV_EXTRAS: tuple[str, ...] = ("httpx", "asgi_lifespan", "pytest", "pytest_asyncio")


@testing_app.command("test:doctor")
def test_doctor() -> None:
    """Preflight the test environment: dev extras, matrix extras, node, browsers, storage, DATABASE_URL."""
    import shutil
    from urllib.parse import urlparse

    from fastplace.cli._doctor import Check, run_checks
    from fastplace.config import config, load_env, reset_config

    root = _project_root()
    load_env(root / ".env")
    reset_config(root)

    def dev_extras() -> list[Check]:
        rows = []
        for name in _DEV_EXTRAS:
            error = _probe_import(name)
            rows.append(
                Check(
                    name=f"dev:{name}",
                    status="pass" if error is None else "fail",
                    detail=error or "importable",
                    fix="" if error is None else "pip install -e '.[dev]'",
                )
            )
        return rows

    def matrix_extras() -> list[Check]:
        # The foundation's Status is pass/warn/fail only — matrix extras are
        # a report, not a demand, so a missing extra warns instead of
        # failing (a doctor of optional backends must never block CI).
        rows = []
        for label, modules, activates in (
            (
                "postgresql",
                ("asyncpg", "pgvector"),
                "portable matrix + tests/orm/postgresql (pgvector extension required)",
            ),
            ("mysql", ("asyncmy",), "portable matrix + tests/orm/mysql"),
            (
                "mongodb",
                ("pymongo",),
                "tests/orm/mongodb contract suite ONLY — NOT in the portable matrix",
            ),
            ("queue", ("saq", "redis"), "queue integration suites"),
        ):
            missing = [m for m in modules if _probe_import(m) is not None]
            rows.append(
                Check(
                    name=f"matrix:{label}",
                    status="pass" if not missing else "warn",
                    detail=activates
                    if not missing
                    else f"not installed ({', '.join(missing)}) — {activates}",
                    fix="" if not missing else f"pip install -e '.[{label}]'",
                )
            )
        return rows

    def node_binaries() -> Check:
        missing = [b for b in ("node", "npm", "npx") if shutil.which(b) is None]
        return Check(
            name="node",
            status="warn" if missing else "pass",
            detail="all on PATH"
            if not missing
            else f"missing: {', '.join(missing)} (test:e2e needs them)",
            fix="" if not missing else "install Node.js (https://nodejs.org)",
        )

    def browsers() -> Check:
        npx = shutil.which("npx")
        if not npx:
            return Check(
                name="browsers",
                status="warn",
                detail="npx absent — cannot probe",
                fix="install Node.js",
            )
        dry = _subprocess_run(
            [npx, "playwright", "install", "--dry-run"], stdout=subprocess.PIPE, text=True
        )
        if _chromium_missing(dry.stdout or ""):
            return Check(
                name="browsers",
                status="warn",
                detail="chromium not installed",
                fix="npx playwright install chromium",
            )
        return Check(name="browsers", status="pass", detail="chromium installed")

    def storage_writable() -> Check:
        ok = _probe_write(root / "storage")
        return Check(
            name="storage",
            status="pass" if ok else "fail",
            detail="writable" if ok else "cannot write under storage/",
            fix="" if ok else "check permissions on storage/",
        )

    def database_url_sanity() -> Check:
        url = str(config("DATABASE_URL", default="sqlite+aiosqlite:///./database.sqlite3") or "")
        shown = _masked_url(url)
        # Conservative default, like every destructive guard in the CLI:
        # an unset APP_ENV reads as production, so the warning fires.
        app_env = str(config("APP_ENV", default="production")).lower()
        host = urlparse(url).hostname or ""
        local = url.startswith("sqlite") or host in ("localhost", "127.0.0.1", "::1", "")
        if app_env == "production":
            return Check(
                name="database-url",
                status="warn",
                detail=f"APP_ENV=production — running tests against {shown}",
                fix="point DATABASE_URL at a scratch DB",
            )
        if not local:
            return Check(
                name="database-url",
                status="warn",
                detail=f"non-local host {shown}",
                fix="tests should target a local/scratch database",
            )
        return Check(name="database-url", status="pass", detail=shown)

    raise typer.Exit(
        code=run_checks(
            "test environment",
            [
                dev_extras,
                matrix_extras,
                node_binaries,
                browsers,
                storage_writable,
                database_url_sanity,
            ],
        )
    )
