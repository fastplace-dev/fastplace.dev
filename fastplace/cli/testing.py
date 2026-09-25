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

    if local_scratch:
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
