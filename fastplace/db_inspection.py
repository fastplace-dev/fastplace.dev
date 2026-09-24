"""Live database inspection and native-shell resolution — behind the db:* CLI.

The CLI layer (``fastplace/cli/db_inspect.py``) stays a thin printer; these
helpers own the real work: the SQLAlchemy inspector round-trips (overview
with optional row counts, per-table describes), the argv for shelling into
the database's native client, and the Mongo document-adapter status.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import inspect
from sqlalchemy.engine import make_url

# ---------------------------------------------------------------------------
# Relational overview (db:show) and describe (db:table)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableOverview:
    """One table in the db:show list — its row count when ``--counts`` asked."""

    name: str
    row_count: int | None = None


@dataclass(frozen=True)
class DatabaseOverview:
    """What db:show prints: connection identity plus the table list."""

    driver: str
    database: str | None
    pool_size: int | None
    tables: list[TableOverview]


@dataclass(frozen=True)
class ColumnDetail:
    """One column of a described table."""

    name: str
    type: str
    nullable: bool
    primary_key: bool
    foreign_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class IndexDetail:
    """One index of a described table."""

    name: str
    columns: tuple[str, ...]
    unique: bool


@dataclass(frozen=True)
class TableDetail:
    """A described table: columns with types/keys plus its indexes."""

    name: str
    columns: list[ColumnDetail]
    indexes: list[IndexDetail]


async def database_overview(*, include_counts: bool = False) -> DatabaseOverview:
    """Snapshot the configured connection: driver, database, pool, tables.

    Row counts run one ``SELECT COUNT(*)`` per table through the dialect's
    identifier preparer, so unusual table names stay safely quoted.
    """
    from fastplace.db import db

    engine = db.manager.engine()

    def _collect(sync_conn) -> list[TableOverview]:
        inspector = inspect(sync_conn)
        preparer = sync_conn.dialect.identifier_preparer
        tables: list[TableOverview] = []
        for name in sorted(inspector.get_table_names()):
            count = None
            if include_counts:
                quoted = preparer.quote(name)
                count = int(
                    sync_conn.exec_driver_sql(f"SELECT COUNT(*) FROM {quoted}").scalar_one()
                )
            tables.append(TableOverview(name=name, row_count=count))
        return tables

    async with db.connection() as session:
        conn = await session.connection()
        tables = await conn.run_sync(_collect)

    # NullPool (and friends) have no size(); only queue-style pools report one.
    size = getattr(engine.pool, "size", None)
    return DatabaseOverview(
        driver=engine.dialect.name,
        database=engine.url.database,
        pool_size=int(size()) if callable(size) else None,
        tables=tables,
    )


async def describe_table(name: str) -> TableDetail | None:
    """Describe one table; ``None`` when it does not exist (db:table exits 1)."""
    from fastplace.db import db

    def _describe(sync_conn) -> TableDetail | None:
        inspector = inspect(sync_conn)
        if name not in inspector.get_table_names():
            return None
        pk = set(inspector.get_pk_constraint(name).get("constrained_columns") or [])
        references: dict[str, set[str]] = {}
        for fk in inspector.get_foreign_keys(name):
            referred = str(fk.get("referred_table") or "")
            for column in fk.get("constrained_columns") or []:
                references.setdefault(column, set()).add(referred)
        columns = [
            ColumnDetail(
                name=str(column["name"]),
                type=str(column["type"]),
                nullable=bool(column.get("nullable", True)),
                primary_key=str(column["name"]) in pk,
                foreign_keys=tuple(sorted(references.get(str(column["name"]), ()))),
            )
            for column in inspector.get_columns(name)
        ]
        indexes = [
            IndexDetail(
                name=str(index.get("name") or "(unnamed)"),
                columns=tuple(str(column) for column in index.get("column_names") or ()),
                unique=bool(index.get("unique")),
            )
            for index in inspector.get_indexes(name)
        ]
        return TableDetail(name=name, columns=columns, indexes=indexes)

    async with db.connection() as session:
        conn = await session.connection()
        return await conn.run_sync(_describe)


# ---------------------------------------------------------------------------
# Native interactive shell (db:cli)
# ---------------------------------------------------------------------------

#: Relational driver → its interactive client binary.
_SHELL_BINARIES = {"sqlite": "sqlite3", "postgresql": "psql", "mysql": "mysql"}

#: Driver → the flag its client spells for the user name.
_USER_FLAGS = {"postgresql": "--username", "mysql": "--user"}


def native_shell_argv() -> list[str]:
    """Argv that opens the configured database's native interactive client.

    The CLI runs it with subprocess (os.execvp semantics: the shell owns the
    terminal) and forwards its exit code. Raises ``ValueError`` for drivers
    without a mapped binary, or a sqlite target with no file to open.
    Credentials are deliberately never placed on the command line — argv is
    visible to every process on the host via ``ps`` — so psql and mysql
    prompt for the password instead.
    """
    from fastplace.db import db
    from fastplace.orm.capabilities import driver_from_url

    cfg = db.manager.config_for("default")
    url_text = str(cfg["url"])
    url = make_url(url_text)
    driver = str(cfg.get("driver") or driver_from_url(url_text))

    if driver not in _SHELL_BINARIES:
        known = ", ".join(sorted(_SHELL_BINARIES))
        raise ValueError(f"no interactive client for driver '{driver}' — supported: {known}")

    if driver == "sqlite":
        path = url.database
        if not path or path == ":memory:":
            raise ValueError("the sqlite database is in-memory — no file for the sqlite3 shell")
        return ["sqlite3", path]

    # psql and mysql take the same explicit-flag form (neither accepts the
    # async URL as-is, and flags keep the password off the command line —
    # only the flag spelling of the user differs). Both clients prompt.
    argv = [_SHELL_BINARIES[driver]]
    if url.host:
        argv.append(f"--host={url.host}")
    if url.port:
        argv.append(f"--port={url.port}")
    if url.username:
        argv.append(f"{_USER_FLAGS[driver]}={url.username}")
    if url.database:
        argv.append(url.database)
    return argv


# ---------------------------------------------------------------------------
# Document adapter status (db:documents)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CollectionStat:
    """One Mongo collection in the db:documents status list."""

    name: str
    documents: int


async def documents_status() -> list[CollectionStat]:
    """Collections (sorted) with estimated document counts from the adapter."""
    # Imported here so tests can monkeypatch the factory symbol (the
    # session:gc convention) — no pymongo client is built unless asked for.
    from fastplace.orm.documents import documents_database

    database = documents_database()
    stats: list[CollectionStat] = []
    for name in sorted(await database.list_collection_names()):
        count = await database[name].estimated_document_count()
        stats.append(CollectionStat(name=name, documents=int(count)))
    return stats
