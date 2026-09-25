"""App-plane auth CLI — token:list, gate:check, auth:sessions, auth:2fa-disable, auth:reset-link."""

from __future__ import annotations

import asyncio
import os
import re

import pytest
from _isolation import isolate_project_state  # noqa: F401  (autouse: db + app.* isolation)
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
runner = CliRunner()


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    token:list bootstraps config via load_env(), and python-dotenv writes the
    cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after: identical
    pattern to tests/cli/test_cache_cmds.py.
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _fresh_auth_ops_state():
    """PAT-store singleton never leaks engines between tests."""
    from fastplace.auth.tokens import reset_pat_store

    reset_pat_store()
    yield
    reset_pat_store()


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A tmp project: isolated sqlite database, no APP_ENV."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/auth-ops.db")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# token:list
# ---------------------------------------------------------------------------


def test_token_list_shows_seeded_rows_with_abilities(project):
    from fastplace.auth.tokens import pat_store

    asyncio.run(pat_store().issue(1, "laptop"))
    asyncio.run(pat_store().issue(2, "ci", abilities=["posts:read"]))

    result = runner.invoke(cli_app, ["token:list"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "Personal access tokens" in plain
    assert "laptop" in plain and "ci" in plain
    assert "posts:read" in plain  # abilities render comma-joined


def test_token_list_scopes_to_one_owner(project):
    from fastplace.auth.tokens import pat_store

    asyncio.run(pat_store().issue(1, "laptop"))
    asyncio.run(pat_store().issue(2, "ci"))

    result = runner.invoke(cli_app, ["token:list", "--user", "1"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "laptop" in plain
    assert "ci" not in plain


def test_token_list_empty_is_dim_not_an_error(project):
    result = runner.invoke(cli_app, ["token:list"])
    assert result.exit_code == 0, result.output
    assert "no personal access tokens" in ANSI_RE.sub("", result.output)


def test_token_list_empty_scoped_names_the_user(project):
    result = runner.invoke(cli_app, ["token:list", "--user", "7"])
    assert result.exit_code == 0, result.output
    assert "for user 7" in ANSI_RE.sub("", result.output)


# ---------------------------------------------------------------------------
# gate:check
# ---------------------------------------------------------------------------

GATES_MODULE = '''"""Project gates — the import_gates() walk imports this module."""

from fastplace.authz.gate import gate


@gate.define("posts.view")
async def view_post(user, post):
    return user["role"] == "editor"


@gate.define("self.access")
async def self_access(user):
    return user["role"] == "editor"
'''


@pytest.fixture()
def gates_project(tmp_path, monkeypatch):
    """A tmp project carrying app/auth/gates.py with two registered abilities."""
    (tmp_path / "asgi.py").write_text("# marker — the _project_root() check\n")
    for package in ("app", "app/auth"):
        (tmp_path / package).mkdir(parents=True, exist_ok=True)
        (tmp_path / package / "__init__.py").write_text("")
    (tmp_path / "app/auth/gates.py").write_text(GATES_MODULE)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class FakeGateRepository:
    """find_by_id seam — the command imports _accounts_repository at call time."""

    def __init__(self, role: str = "editor") -> None:
        self.role = role

    async def find_by_id(self, user_id):
        return {"id": user_id, "role": self.role}


@pytest.fixture(autouse=True)
def _clean_gate_registry():
    from fastplace.authz.gate import gate

    gate.reset_shared()
    yield
    gate.reset_shared()


def test_gate_check_allow_prints_source_and_exits_zero(gates_project, monkeypatch):
    import fastplace.cli.provisioning as provisioning

    monkeypatch.setattr(provisioning, "_accounts_repository", lambda: FakeGateRepository("editor"))

    result = runner.invoke(cli_app, ["gate:check", "1", "posts.view", "--arg", '{"post": 5}'])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "allow" in plain
    assert "posts.view" in plain


def test_gate_check_deny_exits_one(gates_project, monkeypatch):
    import fastplace.cli.provisioning as provisioning

    monkeypatch.setattr(provisioning, "_accounts_repository", lambda: FakeGateRepository("viewer"))

    result = runner.invoke(cli_app, ["gate:check", "1", "posts.view", "--arg", '{"post": 5}'])

    assert result.exit_code == 1
    assert "deny" in ANSI_RE.sub("", result.output)


def test_gate_check_unknown_ability_exits_one(gates_project, monkeypatch):
    import fastplace.cli.provisioning as provisioning

    monkeypatch.setattr(provisioning, "_accounts_repository", lambda: FakeGateRepository("editor"))

    result = runner.invoke(cli_app, ["gate:check", "1", "teapot.brew"])

    assert result.exit_code == 1
    assert "not registered" in ANSI_RE.sub("", result.output)


def test_gate_check_unknown_user_exits_one(gates_project, monkeypatch):
    import fastplace.cli.provisioning as provisioning

    class Missing:
        async def find_by_id(self, user_id):
            return None

    monkeypatch.setattr(provisioning, "_accounts_repository", lambda: Missing())

    result = runner.invoke(cli_app, ["gate:check", "99", "self.access"])

    assert result.exit_code == 1
    assert "99" in ANSI_RE.sub("", result.output)


def test_gate_check_bad_arg_json_exits_one(gates_project, monkeypatch):
    import fastplace.cli.provisioning as provisioning

    monkeypatch.setattr(provisioning, "_accounts_repository", lambda: FakeGateRepository("editor"))

    result = runner.invoke(cli_app, ["gate:check", "1", "posts.view", "--arg", "{not json"])

    assert result.exit_code == 1
    assert "invalid --arg JSON" in ANSI_RE.sub("", result.output)


def test_gate_check_without_accounts_module_exits_one(gates_project, monkeypatch):
    import fastplace.cli.provisioning as provisioning

    def _missing():
        raise ImportError("No module named 'app.modules.accounts'")

    monkeypatch.setattr(provisioning, "_accounts_repository", _missing)

    result = runner.invoke(cli_app, ["gate:check", "1", "self.access"])

    assert result.exit_code == 1
    assert "accounts" in ANSI_RE.sub("", result.output)


# ---------------------------------------------------------------------------
# auth:sessions
# ---------------------------------------------------------------------------


def _write_session(session_id: str, user_id: int) -> None:
    from fastplace.http.session.database import DatabaseSessionStore

    asyncio.run(DatabaseSessionStore().write(session_id, {"k": 1}, user_id=user_id))


def test_sessions_database_lists_only_that_users_sessions(project, monkeypatch):
    monkeypatch.setenv("SESSION_DRIVER", "database")
    _write_session("sess-a", 7)
    _write_session("sess-b", 8)

    result = runner.invoke(cli_app, ["auth:sessions", "7"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "sess-a" in plain
    assert "sess-b" not in plain
    assert "not populated" in plain  # the ip/user-agent limitation note


def test_sessions_database_excludes_expired_rows(project, monkeypatch):
    """Review Focus #2 through the CLI: a row past the lifetime is missing."""
    import time

    monkeypatch.setenv("SESSION_DRIVER", "database")
    _write_session("stale", 7)
    import sqlalchemy as sa

    from fastplace.http.session.base import session_lifetime
    from fastplace.http.session.database import DatabaseSessionStore, sessions_table

    store = DatabaseSessionStore()

    async def _age_out() -> None:
        async with store._engine().begin() as conn:
            await conn.execute(
                sa.update(sessions_table)
                .where(sessions_table.c.id == "stale")
                .values(last_activity=int(time.time()) - session_lifetime() - 10)
            )

    asyncio.run(_age_out())

    result = runner.invoke(cli_app, ["auth:sessions", "7"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "stale" not in plain
    assert "no active sessions" in plain


def test_sessions_empty_is_dim_exit_zero(project, monkeypatch):
    monkeypatch.setenv("SESSION_DRIVER", "database")

    result = runner.invoke(cli_app, ["auth:sessions", "9"])

    assert result.exit_code == 0, result.output
    assert "no active sessions" in ANSI_RE.sub("", result.output)


def test_sessions_memory_states_the_limitation(project, monkeypatch):
    monkeypatch.setenv("SESSION_DRIVER", "memory")

    result = runner.invoke(cli_app, ["auth:sessions", "7"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "inside the server process" in plain


def test_sessions_redis_enumerates_via_client_seam(project, monkeypatch):
    class FakeScanRedis:
        """set/get/scan_iter — the enumeration surface."""

        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def set(self, key, value, ex=None):
            self.store[key] = value

        async def get(self, key):
            return self.store.get(key)

        async def scan_iter(self, match=None):
            import fnmatch

            for key in list(self.store):
                if match is None or fnmatch.fnmatch(key, match):
                    yield key

    from fastplace.http import session as session_module
    from fastplace.http.session.redis_store import RedisSessionStore

    redis = FakeScanRedis()
    monkeypatch.setenv("SESSION_DRIVER", "redis")
    monkeypatch.setattr(
        session_module, "session_store", lambda config_get=None: RedisSessionStore(client=redis)
    )
    seeded = RedisSessionStore(client=redis)
    asyncio.run(seeded.write("sid-1", {"k": 1}, user_id=7))
    asyncio.run(seeded.write("sid-2", {"k": 2}, user_id=8))

    result = runner.invoke(cli_app, ["auth:sessions", "7"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "sid-1" in plain
    assert "sid-2" not in plain


def test_sessions_store_error_exits_one(project, monkeypatch):
    monkeypatch.setenv("SESSION_DRIVER", "database")

    def _boom(config_get=None):
        raise RuntimeError("db unreachable")

    from fastplace.http import session as session_module

    monkeypatch.setattr(session_module, "session_store", _boom)

    result = runner.invoke(cli_app, ["auth:sessions", "7"])

    assert result.exit_code == 1
    assert "db unreachable" in ANSI_RE.sub("", result.output)
