"""App-plane auth CLI — token:list, gate:check, auth:sessions, auth:2fa-disable, auth:reset-link."""

from __future__ import annotations

import asyncio
import os
import re
from types import SimpleNamespace

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
    """PAT-store and reset-token-store singletons never leak engines between tests."""
    from fastplace.auth.passwords import reset_token_store
    from fastplace.auth.tokens import reset_pat_store

    reset_pat_store()
    reset_token_store()
    yield
    reset_pat_store()
    reset_token_store()


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


# ---------------------------------------------------------------------------
# auth:2fa-disable — destructive guard + column wipe + login revocation
# ---------------------------------------------------------------------------


class FakeTwoFactorUser:
    """The scaffolded user's 2FA surface — three columns + save()."""

    def __init__(self, user_id: int = 5, secret: str | None = "enc-secret") -> None:
        self.id = user_id
        self.email = "ada@example.com"
        self.two_factor_secret = secret
        self.two_factor_recovery_codes = "enc-codes" if secret else None
        self.two_factor_confirmed_at = 1690000000 if secret else None
        self.saves = 0

    async def save(self) -> None:
        self.saves += 1


class FakeTwoFactorRepository:
    def __init__(self, user) -> None:
        self.user = user

    async def find_by_id(self, user_id):
        return self.user if self.user.id == user_id else None


class FakeRememberStore:
    def __init__(self) -> None:
        self.revoked_users: list[int] = []

    async def revoke_all_for_user(self, user_id):
        self.revoked_users.append(user_id)
        return 3


class FakeDestroySessionStore:
    def __init__(self) -> None:
        self.destroyed: list[int] = []

    async def destroy_for_user(self, user_id, *, except_session_id=None):
        self.destroyed.append(user_id)
        return 2


@pytest.fixture()
def two_factor_seam(monkeypatch):
    """Wire repo/remember/session seams; returns (user, remember, sessions).

    Full dotted-path imports on purpose: isolate_project_state's teardown
    evicts these modules from sys.modules after each test, and the
    ``from package import module`` form would resolve through the parent
    package's stale attribute — patching a dead module object while the
    command re-imports a fresh one. ``import a.b as x`` re-hydrates the
    sys.modules entry, so fixture and command share one module object.
    """
    import fastplace.auth.remember as remember_module
    import fastplace.cli.provisioning as provisioning
    import fastplace.http.session as session_module

    user = FakeTwoFactorUser()
    remember = FakeRememberStore()
    sessions = FakeDestroySessionStore()
    monkeypatch.setattr(
        provisioning, "_accounts_repository", lambda: FakeTwoFactorRepository(user)
    )
    monkeypatch.setattr(remember_module, "remember_store", lambda: remember)
    monkeypatch.setattr(session_module, "session_store", lambda config_get=None: sessions)
    return user, remember, sessions


def test_2fa_disable_guard_blocks_in_production(project, monkeypatch, two_factor_seam):
    user, remember, sessions = two_factor_seam
    monkeypatch.setenv("APP_ENV", "production")

    result = runner.invoke(cli_app, ["auth:2fa-disable", "5"], input="n\n")

    assert result.exit_code == 1
    plain = ANSI_RE.sub("", result.output)
    assert "aborted" in plain and "left untouched" in plain
    assert user.saves == 0 and user.two_factor_secret == "enc-secret"  # nothing cleared
    assert remember.revoked_users == [] and sessions.destroyed == []


def test_2fa_disable_force_clears_columns_and_revokes(project, monkeypatch, two_factor_seam):
    user, remember, sessions = two_factor_seam
    monkeypatch.setenv("APP_ENV", "production")

    result = runner.invoke(cli_app, ["auth:2fa-disable", "5", "--force"])

    assert result.exit_code == 0, result.output
    assert user.two_factor_secret is None
    assert user.two_factor_recovery_codes is None
    assert user.two_factor_confirmed_at is None
    assert user.saves == 1
    assert remember.revoked_users == [5] and sessions.destroyed == [5]
    plain = ANSI_RE.sub("", result.output)
    assert "3" in plain and "2" in plain  # both counts reported
    assert "two_factor_secret" in plain


def test_2fa_disable_outside_production_skips_the_prompt(project, two_factor_seam):
    user, _, _ = two_factor_seam  # APP_ENV unset → local; no stdin needed

    result = runner.invoke(cli_app, ["auth:2fa-disable", "5"])

    assert result.exit_code == 0, result.output
    assert user.two_factor_secret is None


def test_2fa_disable_without_configuration_is_dim(project, monkeypatch, two_factor_seam):
    user, remember, sessions = two_factor_seam
    user.two_factor_secret = None
    user.two_factor_recovery_codes = None
    user.two_factor_confirmed_at = None

    result = runner.invoke(cli_app, ["auth:2fa-disable", "5"])

    assert result.exit_code == 0, result.output
    assert "no two-factor configuration to clear" in ANSI_RE.sub("", result.output)
    assert user.saves == 0 and remember.revoked_users == [] and sessions.destroyed == []


def test_2fa_disable_unknown_user_exits_one(project, monkeypatch, two_factor_seam):
    monkeypatch.setattr(
        two_factor_seam[0], "id", 99, raising=False
    )  # repo only matches id 5 → 5 is now unknown

    result = runner.invoke(cli_app, ["auth:2fa-disable", "5"])

    assert result.exit_code == 1
    assert "no user with id 5" in ANSI_RE.sub("", result.output)


# ---------------------------------------------------------------------------
# auth:reset-link — reissue with one-live-token semantics
# ---------------------------------------------------------------------------


class FakeResetRepository:
    """find_by_id / find_by_email seam — ids 5 and email ada@example.com."""

    def __init__(self) -> None:
        self.user = SimpleNamespace(id=5, email="ada@example.com")

    async def find_by_id(self, user_id):
        return self.user if user_id == 5 else None

    async def find_by_email(self, email):
        return self.user if email == "ada@example.com" else None


@pytest.fixture()
def reset_seam(monkeypatch):
    import fastplace.cli.provisioning as provisioning

    repository = FakeResetRepository()
    monkeypatch.setattr(provisioning, "_accounts_repository", lambda: repository)
    return repository


def _live_reset_rows() -> int:
    from sqlalchemy import func, select

    from fastplace.auth.passwords import password_reset_tokens, token_store

    async def _count() -> int:
        store = token_store()
        # The guard-abort path never issues, so the table may not exist yet —
        # _ensure_table is idempotent (checkfirst) and makes count() safe.
        await store._ensure_table()
        async with store._engine().connect() as conn:
            return int(
                (
                    await conn.execute(select(func.count()).select_from(password_reset_tokens))
                ).scalar()
            )

    return asyncio.run(_count())


def test_reset_link_by_id_prints_url_and_invalidation_note(project, reset_seam):
    result = runner.invoke(cli_app, ["auth:reset-link", "5"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "/reset-password/" in plain
    assert "email=ada%40example.com" in plain
    assert "previously issued reset link" in plain and "invalid" in plain


def test_reset_link_by_email_resolves_the_same_user(project, reset_seam):
    result = runner.invoke(cli_app, ["auth:reset-link", "ada@example.com"])

    assert result.exit_code == 0, result.output
    assert "/reset-password/" in ANSI_RE.sub("", result.output)


def test_reset_link_reissue_invalidates_prior_link(project, reset_seam):
    """Review Focus #5: two runs leave exactly ONE live token row."""
    assert runner.invoke(cli_app, ["auth:reset-link", "5"]).exit_code == 0
    assert runner.invoke(cli_app, ["auth:reset-link", "5"]).exit_code == 0

    assert _live_reset_rows() == 1


def test_reset_link_send_dispatches_through_mail_facade(project, monkeypatch, reset_seam):
    monkeypatch.setenv("MAIL_DRIVER", "memory")

    result = runner.invoke(cli_app, ["auth:reset-link", "5", "--send"])

    assert result.exit_code == 0, result.output
    from fastplace.mail import mail_outbox

    outbox = mail_outbox()
    assert len(outbox) == 1
    assert outbox[0].to == "ada@example.com"
    plain = ANSI_RE.sub("", result.output)
    assert "dispatched" in plain  # the side effect is stated


def test_reset_link_guard_blocks_in_production(project, monkeypatch, reset_seam):
    monkeypatch.setenv("APP_ENV", "production")

    result = runner.invoke(cli_app, ["auth:reset-link", "5"], input="n\n")

    assert result.exit_code == 1
    assert "aborted" in ANSI_RE.sub("", result.output)
    assert _live_reset_rows() == 0  # nothing was issued


def test_reset_link_unknown_user_exits_one(project, reset_seam):
    result = runner.invoke(cli_app, ["auth:reset-link", "999"])

    assert result.exit_code == 1
    assert "999" in ANSI_RE.sub("", result.output)


def test_reset_link_non_numeric_non_email_exits_one(project, reset_seam):
    result = runner.invoke(cli_app, ["auth:reset-link", "not-an-email"])

    assert result.exit_code == 1
    assert "user id or email" in ANSI_RE.sub("", result.output)
