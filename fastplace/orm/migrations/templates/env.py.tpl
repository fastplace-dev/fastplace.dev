"""Alembic migration environment — Fastplace-managed (async engine).

Scaffolded by `fastplace db:configure`; safe to extend but the model
discovery + DATABASE_URL wiring below is what autogenerate relies on.
"""

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make project modules (app/, config/) importable regardless of cwd.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastplace.config import config, load_env  # noqa: E402
from fastplace.orm.migrations.include import include_object  # noqa: E402
from fastplace.orm.manager import normalize_database_url  # noqa: E402
from fastplace.orm import Model  # noqa: E402
from fastplace.orm.registry import import_all_models  # noqa: E402

alembic_config = context.config

if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

# Same config source as the running app: a bare `alembic upgrade` (no
# fastplace CLI involved) must still resolve the .env DATABASE_URL. Never
# overrides a real environment variable.
load_env(PROJECT_ROOT / ".env")

# Discover declared models so autogenerate diffs against full metadata.
import_all_models(PROJECT_ROOT)
target_metadata = Model.metadata

# Bare schemes (mysql://, postgresql://) bind to the async driver before
# Alembic builds its engine — same contract as the runtime DatabaseManager.
database_url = normalize_database_url(
    str(config("DATABASE_URL", default="sqlite+aiosqlite:///./database.sqlite3"))
)
alembic_config.set_main_option("sqlalchemy.url", database_url)

# Framework bookkeeping tables and reflected ANN indexes are excluded by the
# one shared hook (fastplace.orm.migrations.include) — the same policy
# `fastplace migrate:check` diffs with, so the two can never disagree.


def _render_item(type_, obj, autogen_context):
    """Reference non-SQLAlchemy column types by an importable module path.

    Alembic emits qualified reprs for custom types (``fastplace.orm.types.
    VectorJSON()``, ``pgvector.sqlalchemy.vector.VECTOR(dim=3)``) but never
    adds the import — and third-party types may render a private module the
    package does not export. Rewrite both cases so revisions actually run.
    """
    import sqlalchemy as sa

    if type_ != "type":
        return False  # fall through to Alembic's default rendering
    module = type(obj).__module__
    if isinstance(obj, sa.types.TypeDecorator) and module.startswith("fastplace."):
        autogen_context.imports.add(f"import {module}")
        return f"{module}.{type(obj).__name__}()"
    if (
        isinstance(obj, sa.types.UserDefinedType)
        and not isinstance(obj, sa.types.TypeDecorator)
        and module.split(".")[0] not in ("sqlalchemy", "alembic", "fastplace")
    ):
        # e.g. pgvector.sqlalchemy.vector.VECTOR -> pgvector.sqlalchemy.VECTOR
        package = module.rsplit(".", 1)[0] if "." in module else module
        autogen_context.imports.add(f"import {package}")
        args = repr(obj)
        args = args[args.index("(") :] if "(" in args else "()"
        return f"{package}.{type(obj).__name__}{args}"
    return False  # fall through to Alembic's default rendering


def run_migrations_offline() -> None:
    """Emit SQL without a live DB connection."""
    url = alembic_config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        render_item=_render_item,
        # SQLite needs batch mode for ALTER TABLE operations.
        render_as_batch=url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        render_item=_render_item,
        render_as_batch=connection.dialect.name == "sqlite",
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connect_args: dict = {}
    if database_url.startswith(("mysql", "mariadb")):
        # Same pin as the runtime engine: a latin1-default server would
        # mangle non-ASCII data inside hand-written data migrations.
        connect_args["charset"] = str(config("DATABASE_CHARSET", default="utf8mb4"))
    connectable = async_engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
