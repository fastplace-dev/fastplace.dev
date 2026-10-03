"""Two-layer read-only guard for `database-query`.

Layer-1 tests are table-driven over the known attack corpus; layer-2 tests run
against real SQLite through the engine-enforced transaction path.
"""

from __future__ import annotations

import pytest
from sqlalchemy import exc as sa_exc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from fastplace.mcp.safety.sql_readonly import (
    detect_engine,
    is_read_only_query,
    run_read_only_select,
)

# ---------------------------------------------------------------- layer 1


READ_ONLY = [
    "SELECT * FROM users",
    "select id from users where email = 'a@b.c'",
    "SHOW TABLES",
    "EXPLAIN SELECT * FROM users",
    "EXPLAIN ANALYZE SELECT * FROM users",
    "DESCRIBE users",
    "DESC users",
    "WITH t AS (SELECT 1 AS n) SELECT n FROM t",
    "VALUES (1), (2)",
    "TABLE users",
    'SELECT * FROM "users; DROP TABLE users" WHERE id = 1',
    "SELECT 'DELETE FROM users; --' FROM users",
    "SELECT REPLACE(name, 'a', 'b') FROM users",
    "SELECT INSERT(name, 1, 2, 'x') FROM users",
    "SELECT * FROM users -- DROP TABLE users",
    "/* comment */ SELECT * FROM users",
    "SELECT * FROM users;",
    "  SELECT  ",
    "with recent AS (SELECT * FROM orders) SELECT * FROM recent",
    "SELECT * FROM users; -- comment only after the statement",
    # A plain block comment is engine-ignored; only /*! */ version comments
    # are executable and rejected.
    "SELECT 1 /*; DROP TABLE users */",
]

NOT_READ_ONLY = [
    "DROP TABLE users",
    "DELETE FROM users",
    "UPDATE users SET name = 'x'",
    "INSERT INTO users VALUES (1)",
    "TRUNCATE users",
    "ALTER TABLE users ADD COLUMN x INT",
    "CREATE TABLE t (id INT)",
    "RENAME TABLE a TO b",
    "MERGE INTO t USING s ON 1=1 WHEN MATCHED THEN UPDATE SET x=1",
    "SELECT * FROM users; DROP TABLE users",
    "SELECT * FROM users; DELETE FROM users",
    "SELECT /*! 50000 DELETE FROM users */ 1",
    "SELECT * FROM users INTO OUTFILE '/tmp/x'",
    "SELECT * INTO new_users FROM users",
    "INSERT INTO t SELECT * FROM users",
    "EXPLAIN ANALYZE DELETE FROM users",
    "EXPLAIN DELETE FROM users",
    "EXPLAIN (ANALYZE, FORMAT JSON) UPDATE t SET x = 1",
    "WITH t AS (SELECT 1) DELETE FROM users",
    "WITH t AS (UPDATE users SET x = 1 RETURNING *) SELECT * FROM t",
    "",
    "   ",
    "COMMIT",
    "BEGIN",
    "SET FOREIGN_KEY_CHECKS=0",
    "PRAGMA journal_mode = wal",
    "CALL do_stuff()",
    "VACUUM",
]


@pytest.mark.parametrize(
    ("query", "expected"), [(q, True) for q in READ_ONLY] + [(q, False) for q in NOT_READ_ONLY]
)
def test_is_read_only_query(query: str, expected: bool) -> None:
    assert is_read_only_query(query) is expected


def test_mysql_backslash_escapes_do_not_hide_writes() -> None:
    # A \' escape must not swallow the rest of the query on MySQL-shaped input.
    assert is_read_only_query(r"SELECT '\'; DELETE FROM users") is False


def test_doubled_quotes_do_not_hide_writes() -> None:
    assert is_read_only_query("SELECT 'it''s'; DROP TABLE users") is False


def test_backslash_escapes_flag_guards_string_scanning() -> None:
    # With MySQL-style escapes the whole tail is one literal (safe structure);
    # without them the literal closes early and the stacked DROP is exposed.
    query = r"SELECT 'a\'b; DROP TABLE users'"
    assert is_read_only_query(query, backslash_escapes=True) is True
    assert is_read_only_query(query, backslash_escapes=False) is False


def test_case_insensitive_keywords() -> None:
    assert is_read_only_query("SeLeCt * FrOm users") is True
    assert is_read_only_query("dRoP TaBlE users") is False


# ---------------------------------------------------------------- layer 2


@pytest.fixture()
async def sqlite_engine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)"))
        await conn.execute(text("INSERT INTO items (name) VALUES ('a'), ('b')"))
    yield engine
    await engine.dispose()


async def test_run_read_only_select_returns_rows(sqlite_engine) -> None:
    rows = await run_read_only_select(sqlite_engine, "SELECT * FROM items ORDER BY id")

    assert rows == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]


async def test_pragma_query_only_blocks_writes_even_if_layer1_bypassed(sqlite_engine) -> None:
    # Layer 2 is the source of truth: prove the connection itself refuses writes.
    # SQLAlchemy re-raises the driver error as its own OperationalError subclass.
    with pytest.raises(sa_exc.OperationalError):
        await run_read_only_select(sqlite_engine, "DELETE FROM items", enforce_only=True)

    async with sqlite_engine.connect() as conn:
        remaining = (await conn.execute(text("SELECT COUNT(*) FROM items"))).scalar()
    assert remaining == 2


async def test_connection_state_restored_after_query(sqlite_engine) -> None:
    await run_read_only_select(sqlite_engine, "SELECT 1")

    async with sqlite_engine.begin() as conn:
        await conn.execute(text("INSERT INTO items (name) VALUES ('c')"))
    async with sqlite_engine.connect() as conn:
        count = (await conn.execute(text("SELECT COUNT(*) FROM items"))).scalar()
    assert count == 3


def test_detect_engine_from_url() -> None:
    assert detect_engine("sqlite+aiosqlite:///x.db") == "sqlite"
    assert detect_engine("postgresql+asyncpg://u:p@h/db") == "postgresql"
    assert detect_engine("mysql+asyncmy://u:p@h/db") == "mysql"
    assert detect_engine("mariadb+asyncmy://u:p@h/db") == "mariadb"
