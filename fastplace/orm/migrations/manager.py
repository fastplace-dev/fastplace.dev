"""MigrationsManager — Alembic wrapped in the Fastplace public API.

Projects keep migrations in ``database/migrations/`` (versions in
``database/migrations/versions/``); the framework owns the environment
template so async engines, model discovery, and SQLite batch mode work
without project boilerplate (blueprint §8).
"""

from __future__ import annotations

import io
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class MigrationsManager:
    """Programmatic Alembic access: scaffold, make, upgrade, downgrade, status."""

    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root).resolve()
        self.migrations_dir = self.root / "database" / "migrations"
        self.versions_dir = self.migrations_dir / "versions"

    # -- state -----------------------------------------------------------------
    @property
    def configured(self) -> bool:
        """True once ``db:configure`` has scaffolded the environment."""
        return (self.migrations_dir / "env.py").exists()

    def require_configured(self) -> None:
        if not self.configured:
            raise RuntimeError(
                "Migrations are not configured — run `fastplace db:configure` first."
            )

    # -- setup ------------------------------------------------------------------
    def scaffold(self) -> list[Path]:
        """Create the migrations environment from framework templates."""
        self.migrations_dir.mkdir(parents=True, exist_ok=True)
        self.versions_dir.mkdir(parents=True, exist_ok=True)

        created: list[Path] = []
        for template_name in ("env.py.tpl", "script.py.mako"):
            target = self.migrations_dir / template_name.replace(".tpl", "")
            if not target.exists():
                shutil.copyfile(_TEMPLATES_DIR / template_name, target)
                created.append(target)
        return created

    # -- alembic plumbing ---------------------------------------------------------
    def _config(self) -> Any:
        from alembic.config import Config

        cfg = Config()
        cfg.set_main_option("script_location", str(self.migrations_dir))
        cfg.set_main_option("version_locations", str(self.versions_dir.relative_to(self.root)))
        # env.py resolves the URL from DATABASE_URL (env/config modules).
        self._ensure_project_importable()
        return cfg

    def _ensure_project_importable(self) -> None:
        root = str(self.root)
        if root not in sys.path:
            sys.path.insert(0, root)

    # -- operations ----------------------------------------------------------------
    def make(self, message: str = "auto") -> Path | None:
        """Autogenerate a revision from the declared models."""
        from alembic import command

        self.require_configured()
        with redirect_stdout(io.StringIO()):
            command.revision(self._config(), message=message, autogenerate=True)
        revisions = sorted(self.versions_dir.glob("*.py"), key=lambda p: p.stat().st_mtime)
        return revisions[-1] if revisions else None

    def upgrade(self) -> bool:
        """Run all pending migrations (``alembic upgrade head``).

        Everything applied is recorded as one batch in the framework-managed
        ``fastplace_migrations`` table, so a subsequent ``downgrade()``
        without steps can revert exactly this batch.
        """
        from alembic import command

        self.require_configured()
        with redirect_stdout(io.StringIO()):
            command.upgrade(self._config(), "head")
        self._sync_tracking()
        return True

    def downgrade(self, steps: int | None = None) -> bool:
        """Revert migrations.

        ``steps=None`` (the CLI default) reverts the *last batch* — every
        revision applied by the most recent ``migrate`` invocation. An integer
        reverts that many individual revisions.
        """
        from alembic import command

        self.require_configured()
        count = steps if steps is not None else self._last_batch_size()
        with redirect_stdout(io.StringIO()):
            command.downgrade(self._config(), f"-{max(1, count)}")
        self._sync_tracking()
        return True

    def reset(self) -> bool:
        """Downgrade to base, rebuild to head — a full schema rebuild."""
        from alembic import command

        self.require_configured()
        with redirect_stdout(io.StringIO()):
            command.downgrade(self._config(), "base")
            command.upgrade(self._config(), "head")
        # The rebuild applied the whole history in this one invocation.
        self._sync_tracking()
        return True

    # -- batch tracking ------------------------------------------------------------
    # `migrate:rollback` reverts "the latest migration batch" (blueprint §7 /
    # CLAUDE.md): every revision applied by the most recent `migrate` run.
    # Alembic alone does not track invocation batches, so the framework keeps
    # a small bookkeeping table next to alembic_version. All bookkeeping rides
    # the configured async engine — sync drivers (psycopg2/pymysql) are never
    # required.

    def _database_url(self) -> str:
        from fastplace.config import config

        return str(config("DATABASE_URL", default="sqlite+aiosqlite:///./database.sqlite3"))

    async def _applied_revisions_async(self) -> set[str]:
        """Every applied revision — the full ancestor chain, not just the head.

        ``get_current_heads()`` returns only the head of each branch; a
        revision is applied iff it is an ancestor of a stored version, so we
        walk the script graph down from each stored version.
        """
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(self._database_url())
        try:
            async with engine.connect() as conn:
                heads = set(
                    await conn.run_sync(lambda c: MigrationContext.configure(c).get_current_heads())
                )
        finally:
            await engine.dispose()
        if not heads:
            return set()
        script = ScriptDirectory.from_config(self._config())
        applied: set[str] = set()
        for head in heads:
            # base="base" walks the full ancestor chain of `head`, inclusive.
            for rev in script.walk_revisions(base="base", head=head):
                applied.add(rev.revision)
        return applied

    def _sync_tracking(self) -> None:
        """Align the batch table with alembic_version (reconciling both ways).

        Revisions no longer applied are forgotten; applied-but-untracked
        revisions (e.g. a crash between upgrade and bookkeeping) are recorded
        into the newest batch so rollback batches never silently shrink.
        """
        _run_async(self._sync_tracking_async())

    async def _sync_tracking_async(self) -> None:
        import datetime

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        applied = await self._applied_revisions_async()
        engine = create_async_engine(self._database_url())
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS fastplace_migrations ("
                        "revision VARCHAR(255) PRIMARY KEY, "
                        "batch INTEGER NOT NULL, "
                        "applied_at VARCHAR(64) NOT NULL)"
                    )
                )
                rows = await conn.execute(text("SELECT revision FROM fastplace_migrations"))
                tracked = {row[0] for row in rows}
                stale = tracked - applied
                if stale:
                    await conn.execute(
                        text("DELETE FROM fastplace_migrations WHERE revision = :r"),
                        [{"r": revision} for revision in sorted(stale)],
                    )
                missing = applied - tracked
                if missing:
                    result = await conn.execute(
                        text("SELECT COALESCE(MAX(batch), 0) FROM fastplace_migrations")
                    )
                    latest = int(result.scalar() or 0)
                    stamp = datetime.datetime.now(datetime.UTC).isoformat()
                    await conn.execute(
                        text(
                            "INSERT INTO fastplace_migrations (revision, batch, applied_at) "
                            "VALUES (:r, :b, :t)"
                        ),
                        [
                            {"r": revision, "b": latest + 1, "t": stamp}
                            for revision in sorted(missing)
                        ],
                    )
        finally:
            await engine.dispose()

    def _last_batch_size(self) -> int:
        """How many revisions the most recent `migrate` applied."""
        return _run_async(self._last_batch_size_async())

    async def _last_batch_size_async(self) -> int:
        from sqlalchemy import inspect, text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(self._database_url())
        try:
            async with engine.connect() as conn:
                if not await conn.run_sync(lambda c: inspect(c).has_table("fastplace_migrations")):
                    return 1
                result = await conn.execute(
                    text("SELECT COALESCE(MAX(batch), 0) FROM fastplace_migrations")
                )
                latest = int(result.scalar() or 0)
                if latest == 0:
                    return 1
                result = await conn.execute(
                    text("SELECT COUNT(*) FROM fastplace_migrations WHERE batch = :b"),
                    {"b": latest},
                )
                return max(1, int(result.scalar() or 0))
        finally:
            await engine.dispose()

    def status(self) -> str:
        """Human-readable current revision + pending history."""
        from alembic import command
        from alembic.script import ScriptDirectory

        if not self.configured:
            return "Migrations are not configured. Run `fastplace db:configure`."

        cfg = self._config()
        script = ScriptDirectory.from_config(cfg)

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            command.current(cfg)
        current_lines = buffer.getvalue().strip()

        head = script.get_current_head()
        revisions = list(script.walk_revisions())

        if not revisions:
            return "No migrations yet — create one with `fastplace make:migration <name>`."

        applied = _run_async(self._applied_revisions_async())

        lines = ["Migration status:"]
        for rev in revisions:
            marker = "[applied]" if rev.revision in applied else "[ pending]"
            lines.append(f"  {marker} {rev.revision[:12]}  {rev.doc}")
        lines.append(f"  head: {head[:12] if head else '(none)'}")
        if current_lines:
            lines.append(f"  {current_lines}")
        return "\n".join(lines)


def _run_async(coro: Any) -> Any:
    """Run one coroutine on a throwaway loop (CLI paths are synchronous)."""
    import asyncio

    return asyncio.run(coro)


def run_seeders(project_root: str | Path) -> list[str]:
    """Import and run every ``database/seeders/*.py`` (module-level ``run()``)."""
    import asyncio
    import importlib.util

    root = Path(project_root).resolve()
    seeders_dir = root / "database" / "seeders"
    if not seeders_dir.is_dir():
        return []

    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    ran: list[str] = []
    for file in sorted(seeders_dir.glob("*.py")):
        if file.name.startswith("_"):
            continue
        module_name = f"_fastplace_seeder_{file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, file)
        if spec is None or spec.loader is None:  # pragma: no cover
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        run = getattr(module, "run", None)
        if run is None:
            continue
        asyncio.run(run())
        ran.append(file.stem)
    return ran
