"""Two-layer read-only guard for ``database-query``.

Layer 1 is a lexical fast-fail: string literals and comments are blanked out
(offsets preserved) and the remaining structure is matched against a strict
allowlist. Lexical parsing can never be exhaustive — data-modifying CTEs,
vendor extensions, and ``INTO OUTFILE``-style shapes are too varied — so layer
2 wraps execution in an engine-enforced read-only transaction and always rolls
back, making the engine itself the source of truth.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_FIRST_TOKEN_RE = re.compile(r"\s*([A-Za-z]+)")
_SELECT_ONLY_TOKENS = frozenset(
    {"SELECT", "SHOW", "EXPLAIN", "DESCRIBE", "DESC", "WITH", "VALUES", "TABLE"}
)
_WRITE_KEYWORDS = r"DELETE|UPDATE|DROP|ALTER|TRUNCATE|RENAME|CREATE|MERGE"
_BOUNDARY_WRITE_RE = re.compile(
    rf"(^|[();])\s*(?:(?:{_WRITE_KEYWORDS})\b|(?:INSERT|REPLACE)\b(?!\s*\)))",
    re.IGNORECASE,
)
# EXPLAIN ANALYZE executes its target, so a write target is a write.
_EXPLAIN_WRITE_RE = re.compile(
    rf"^\s*EXPLAIN\s+"
    rf"(?:\([^)]*\)\s*|(?:ANALYZE|VERBOSE|QUERY\s+PLAN|FORMAT\s*=?\s*\w+)\s+)*"
    rf"(?:{_WRITE_KEYWORDS}|INSERT|REPLACE)\b",
    re.IGNORECASE,
)
_WITH_SELECT_RE = re.compile(r"\)\s*SELECT\b", re.IGNORECASE)
_INTO_RE = re.compile(r"\bINTO\b", re.IGNORECASE)
_STACKED_RE = re.compile(r";\s*\S")
_VERSION_COMMENT = "/*!"

_MYSQL_DIALECTS = frozenset({"mysql", "mariadb"})
_MYSQL_DEFAULT_ISOLATION = "REPEATABLE READ"


class ReadOnlyViolation(ValueError):
    """The query failed the layer-1 read-only check."""


def detect_engine(database_url: str) -> str:
    """Map a SQLAlchemy database URL to a plain engine name."""
    scheme = database_url.split("://", 1)[0].split("+", 1)[0].lower()
    return scheme


def strip_literals_and_comments(query: str, *, backslash_escapes: bool) -> tuple[str, bool]:
    """Blank out literals and comments, preserving offsets.

    Returns the blanked structure and whether the query contains a MySQL
    version-gated comment (``/*! ... */``), which the engine would execute.
    """
    out: list[str] = []
    has_version_comment = False
    i = 0
    n = len(query)
    state = "code"
    while i < n:
        ch = query[i]
        if state == "code":
            if ch == "'":
                state = "'"
                out.append(" ")
            elif ch == '"':
                state = '"'
                out.append(" ")
            elif ch == "`":
                state = "`"
                out.append(" ")
            elif ch == "-" and query[i : i + 2] == "--":
                state = "--"
                out.append("  ")
                i += 2
                continue
            elif ch == "#" and backslash_escapes:
                # MySQL line comment; PostgreSQL treats # as an operator, so
                # only engines with backslash escapes get this rule.
                state = "--"
                out.append(" ")
            elif ch == "/" and query[i : i + 2] == "/*":
                if query[i : i + 3] == _VERSION_COMMENT:
                    has_version_comment = True
                state = "/*"
                out.append("  ")
                i += 2
                continue
            else:
                out.append(ch)
            i += 1
        elif state in ("'", '"', "`"):
            quote = state
            if backslash_escapes and ch == "\\":
                out.append("  ")
                i += 2
                continue
            if ch == quote:
                if i + 1 < n and query[i + 1] == quote:
                    out.append("  ")  # doubled quote stays inside the literal
                    i += 2
                    continue
                state = "code"
                out.append(" ")
            else:
                out.append(" ")
            i += 1
        elif state == "--":
            if ch == "\n":
                state = "code"
                out.append("\n")
            else:
                out.append(" ")
            i += 1
        else:  # block comment
            if ch == "*" and query[i : i + 2] == "*/":
                state = "code"
                out.append("  ")
                i += 2
                continue
            out.append(" ")
            i += 1
    return "".join(out), has_version_comment


def is_read_only_query(query: str, *, backslash_escapes: bool = False) -> bool:
    """Layer-1 check: does the query look read-only? Fast-fail only."""
    if not query or not query.strip():
        return False

    structure, has_version_comment = strip_literals_and_comments(
        query, backslash_escapes=backslash_escapes
    )
    if has_version_comment:
        # MySQL executes /*! ... */ version-gated comments.
        return False
    if _STACKED_RE.search(structure):
        # One trailing semicolon is fine; stacked statements are not.
        return False

    match = _FIRST_TOKEN_RE.match(structure)
    if match is None or match.group(1).upper() not in _SELECT_ONLY_TOKENS:
        return False

    first_word = match.group(1).upper()
    if first_word == "WITH" and not _WITH_SELECT_RE.search(structure):
        # Data-modifying CTEs are writes.
        return False
    if _BOUNDARY_WRITE_RE.search(structure):
        return False
    if first_word == "EXPLAIN" and _EXPLAIN_WRITE_RE.match(structure):
        return False
    if _INTO_RE.search(structure):
        # SELECT ... INTO / INTO OUTFILE / INTO DUMPFILE.
        return False
    return True


async def run_read_only_select(
    engine: AsyncEngine,
    query: str,
    *,
    enforce_only: bool = False,
) -> list[dict[str, Any]]:
    """Run ``query`` through both guard layers and return rows as dicts.

    ``enforce_only`` skips the layer-1 lexical check (used by tests to prove
    the engine-level layer stands on its own, and available to callers that
    have already validated the query).
    """
    dialect = engine.dialect.name
    backslash_escapes = dialect in _MYSQL_DIALECTS
    if not enforce_only and not is_read_only_query(query, backslash_escapes=backslash_escapes):
        raise ReadOnlyViolation(
            "Only read-only queries are allowed (SELECT, SHOW, EXPLAIN, DESCRIBE)."
        )

    conn = await engine.connect()
    try:
        if dialect == "sqlite":
            # query_only is a connection flag: the PRAGMA itself runs in
            # driver-level autocommit (pysqlite only opens transactions for
            # DML), but SQLAlchemy still opens an autobegin transaction for
            # the statement — clear it before opening the guarded one, and
            # restore the flag before the pooled connection is reused.
            await conn.execute(text("PRAGMA query_only = ON"))
            await conn.rollback()
            trans = await conn.begin()
            try:
                result = await conn.execute(text(query))
                rows = [dict(row) for row in result.mappings().all()]
            finally:
                await trans.rollback()
            await conn.execute(text("PRAGMA query_only = OFF"))
            await conn.rollback()
            return rows

        if dialect in _MYSQL_DIALECTS:
            # Transaction characteristics must be declared before BEGIN, so
            # issue the SET on an autocommit connection, then restore the
            # default isolation level for the guarded transaction itself.
            default_isolation = await conn.run_sync(
                lambda sync_conn: sync_conn.get_isolation_level()
            )
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execution_options(
                isolation_level=default_isolation or _MYSQL_DEFAULT_ISOLATION
            )
            trans = await conn.begin()
            try:
                result = await conn.execute(text(query))
                rows = [dict(row) for row in result.mappings().all()]
            finally:
                await trans.rollback()
            return rows

        # PostgreSQL and everything else dialect-supported: read-only inside
        # the transaction, then roll back unconditionally.
        trans = await conn.begin()
        try:
            if dialect == "postgresql":
                await conn.execute(text("SET TRANSACTION READ ONLY"))
            result = await conn.execute(text(query))
            rows = [dict(row) for row in result.mappings().all()]
        finally:
            await trans.rollback()
        return rows
    finally:
        await conn.close()
