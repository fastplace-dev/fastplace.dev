"""Task 20 CLI — `mail:test`, `token:create`, `token:revoke`, `user:create`.

Provisioning surface (spec #44–#47). Token commands route through the PAT
store — faked for routing assertions, plus real-store lifecycle coverage
against tmp sqlite; user:create goes through the project's accounts
repository — faked for behavior, plus one end-to-end pass through a tmp
project scaffolded with the make:auth templates. Secrets discipline
throughout: the plaintext token is printed exactly once and passwords
never appear in any output.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from _isolation import isolate_project_state  # noqa: F401
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Rich colorizes output when the environment forces color; strip codes so
# assertions match on plain text.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fresh_provisioning_state():
    """PAT-store singleton and mail outbox never leak between tests."""
    from fastplace.auth.tokens import reset_pat_store
    from fastplace.mail import clear_mail_outbox

    reset_pat_store()
    clear_mail_outbox()
    yield
    reset_pat_store()
    clear_mail_outbox()


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A tmp project: isolated sqlite database, memory mail driver."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/provisioning.db")
    monkeypatch.setenv("MAIL_DRIVER", "memory")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# fakes — the PAT store and the accounts repository, with call journals
# ---------------------------------------------------------------------------


class FakePatStore:
    """Records issue/revoke calls; mirrors PersonalAccessTokenStore's surface."""

    def __init__(self) -> None:
        self.issued: list[dict[str, object]] = []
        self.revoked: list[tuple[int, int]] = []
        self.revoked_users: list[int] = []
        self.global_wipes = 0
        self._next_id = 41
        self._owners = {41: 1, 42: 1, 43: 2}

    async def issue(self, user_id, name, *, abilities=None, expires_at=None):
        self.issued.append({"user_id": user_id, "name": name, "abilities": abilities})
        token_id = self._next_id
        self._next_id += 1
        return f"{token_id}|plain-secret-{token_id}"

    async def owner_of(self, token_id):
        return self._owners.get(int(token_id))

    async def revoke(self, token_id, user_id):
        self.revoked.append((int(token_id), int(user_id)))
        return True

    async def revoke_all_for_user(self, user_id):
        self.revoked_users.append(int(user_id))
        return 2

    async def revoke_all(self):
        self.global_wipes += 1
        return 5


@pytest.fixture()
def fake_store(monkeypatch):
    """Route every pat_store() call (create_token included) to the fake."""
    from fastplace.auth import tokens as tokens_module

    fake = FakePatStore()
    monkeypatch.setattr(tokens_module, "pat_store", lambda: fake)
    return fake


class FakeUserRepository:
    """Mirrors the accounts UserRepository contract, hashing via Hash."""

    def __init__(self) -> None:
        self.created: list[dict[str, str]] = []
        self.emails: set[str] = set()

    async def find_by_email(self, email: str):
        return email if email in self.emails else None

    async def create_user(self, *, name: str, email: str, password: str, is_admin: bool = False):
        from fastplace.auth.hashing import Hash

        self.created.append(
            {
                "name": name,
                "email": email,
                "password": password,
                "digest": Hash.make(password),
                "is_admin": is_admin,
            }
        )
        self.emails.add(email)
        return SimpleNamespace(id=len(self.created), name=name, email=email, is_admin=is_admin)


@pytest.fixture()
def fake_repo(monkeypatch):
    """Swap the command's repository resolver for the fake."""
    import fastplace.cli.provisioning as provisioning

    repository = FakeUserRepository()
    monkeypatch.setattr(provisioning, "_accounts_repository", lambda: repository)
    return repository


# ---------------------------------------------------------------------------
# mail:test — one probe through the active transport
# ---------------------------------------------------------------------------


def test_mail_test_sends_probe_via_memory_transport(project):
    result = runner.invoke(cli_app, ["mail:test", "probe@example.com"])
    assert result.exit_code == 0, result.output

    from fastplace.mail import mail_outbox

    outbox = mail_outbox()
    assert len(outbox) == 1
    assert outbox[0].to == "probe@example.com"
    assert outbox[0].subject  # a real message, not an empty shell
    plain = ANSI_RE.sub("", result.output)
    assert "probe@example.com" in plain
    assert "memory" in plain  # the report names the active transport


def test_mail_test_unknown_driver_exits_one(project, monkeypatch):
    monkeypatch.setenv("MAIL_DRIVER", "bogus")

    result = runner.invoke(cli_app, ["mail:test", "probe@example.com"])
    assert result.exit_code == 1
    assert "bogus" in ANSI_RE.sub("", result.output)


# ---------------------------------------------------------------------------
# token:create — plaintext returned exactly once
# ---------------------------------------------------------------------------


def test_token_create_returns_plaintext_once_and_records_row(project, fake_store):
    result = runner.invoke(cli_app, ["token:create", "1", "ci-token"])
    assert result.exit_code == 0, result.output

    assert fake_store.issued == [{"user_id": 1, "name": "ci-token", "abilities": None}]
    token = "41|plain-secret-41"
    plain = ANSI_RE.sub("", result.output)
    assert token in plain
    assert plain.count(token) == 1  # shown exactly once, never repeated
    assert "cannot be shown again" in plain  # the operator is warned


def test_token_create_defaults_the_token_name(project, fake_store):
    result = runner.invoke(cli_app, ["token:create", "3"])
    assert result.exit_code == 0, result.output

    assert fake_store.issued[0]["name"] == "cli"  # sensible default label


# ---------------------------------------------------------------------------
# token:revoke — three forms, routed to the store
# ---------------------------------------------------------------------------


def test_token_revoke_single_by_bare_id_resolves_owner(project, fake_store):
    result = runner.invoke(cli_app, ["token:revoke", "42"])
    assert result.exit_code == 0, result.output

    # The owner is resolved from the row, then the owner-scoped delete runs.
    assert fake_store.revoked == [(42, 1)]


def test_token_revoke_single_with_user_option_scopes_delete(project, fake_store):
    result = runner.invoke(cli_app, ["token:revoke", "43", "--user", "2"])
    assert result.exit_code == 0, result.output

    assert fake_store.revoked == [(43, 2)]


def test_token_revoke_unknown_id_exits_one(project, fake_store):
    result = runner.invoke(cli_app, ["token:revoke", "999"])
    assert result.exit_code == 1
    assert "999" in result.output
    assert fake_store.revoked == []


def test_token_revoke_all_for_user_reports_count(project, fake_store):
    result = runner.invoke(cli_app, ["token:revoke", "--user", "1"])
    assert result.exit_code == 0, result.output

    assert fake_store.revoked_users == [1]
    assert "2" in ANSI_RE.sub("", result.output)


def test_token_revoke_all_purges_every_token(project, fake_store):
    result = runner.invoke(cli_app, ["token:revoke", "--all"])
    assert result.exit_code == 0, result.output

    assert fake_store.global_wipes == 1
    assert fake_store.revoked == []
    assert fake_store.revoked_users == []
    assert "5" in ANSI_RE.sub("", result.output)


def test_token_revoke_requires_a_target(project, fake_store):
    result = runner.invoke(cli_app, ["token:revoke"])
    assert result.exit_code == 1
    assert fake_store.revoked == [] and fake_store.revoked_users == []


def test_token_revoke_rejects_id_with_all(project, fake_store):
    result = runner.invoke(cli_app, ["token:revoke", "41", "--all"])
    assert result.exit_code == 1
    assert fake_store.revoked == [] and fake_store.global_wipes == 0


# ---------------------------------------------------------------------------
# token store additions — real rows in tmp sqlite (owner_of, revoke_all)
# ---------------------------------------------------------------------------


def test_token_lifecycle_against_real_store(project):
    from fastplace.auth.tokens import create_token, pat_store

    bearer = asyncio.run(create_token(7, "real-token"))
    token_id = int(bearer.partition("|")[0])
    store = pat_store()

    assert asyncio.run(store.owner_of(token_id)) == 7
    assert asyncio.run(store.revoke(token_id, 7)) is True
    assert asyncio.run(store.owner_of(token_id)) is None  # hard delete
    assert asyncio.run(store.revoke(token_id, 7)) is False


def test_revoke_all_against_real_store(project):
    from fastplace.auth.tokens import create_token, pat_store

    asyncio.run(create_token(1, "one"))
    asyncio.run(create_token(2, "two"))

    assert asyncio.run(pat_store().revoke_all()) == 2


# ---------------------------------------------------------------------------
# user:create — flags, prompts, duplicate guard, secrets discipline
# ---------------------------------------------------------------------------


def test_user_create_with_flags_creates_via_repository(project, fake_repo):
    result = runner.invoke(
        cli_app,
        ["user:create", "--name", "Ada", "--email", "ada@example.com", "--password", "s3cret-pass"],
    )
    assert result.exit_code == 0, result.output

    assert len(fake_repo.created) == 1
    record = fake_repo.created[0]
    assert record["name"] == "Ada"
    assert record["email"] == "ada@example.com"

    from fastplace.auth.hashing import Hash

    assert Hash.check("s3cret-pass", record["digest"])  # Hash is in the flow

    plain = ANSI_RE.sub("", result.output)
    assert "ada@example.com" in plain
    assert "Ada" in plain
    assert "s3cret-pass" not in plain  # the password never prints


def test_user_create_duplicate_email_exits_one(project, fake_repo):
    args = [
        "user:create",
        "--name",
        "Ada",
        "--email",
        "ada@example.com",
        "--password",
        "s3cret-pass",
    ]
    assert runner.invoke(cli_app, args).exit_code == 0

    result = runner.invoke(cli_app, args)
    assert result.exit_code == 1
    assert "already exists" in ANSI_RE.sub("", result.output)
    assert len(fake_repo.created) == 1  # the second create never ran


def test_user_create_loses_the_duplicate_race_gracefully(project, monkeypatch):
    """Check-then-insert race (final review): another process inserts the row
    between find_by_email and create_user, so the UNIQUE constraint fires.
    The command must answer with the SAME friendly duplicate-email message and
    exit 1 — never surface a raw IntegrityError traceback."""
    from sqlalchemy.exc import IntegrityError

    import fastplace.cli.provisioning as provisioning

    class RacingRepository(FakeUserRepository):
        """find_by_email misses (the rival's insert is uncommitted), then the
        insert itself loses the race at the flush."""

        async def create_user(self, *, name, email, password, is_admin=False):
            raise IntegrityError(
                "INSERT INTO users ...", {}, Exception("UNIQUE constraint failed: users.email")
            )

    repository = RacingRepository()
    monkeypatch.setattr(provisioning, "_accounts_repository", lambda: repository)

    result = runner.invoke(
        cli_app,
        ["user:create", "--name", "Ada", "--email", "ada@example.com", "--password", "s3cret-pass"],
    )
    assert result.exit_code == 1
    assert not isinstance(result.exception, IntegrityError)
    plain = ANSI_RE.sub("", result.output)
    assert "a user with email ada@example.com already exists" in plain
    assert "IntegrityError" not in plain  # the raw race never leaks to the console


def test_user_create_prompts_for_missing_fields(project, fake_repo):
    result = runner.invoke(cli_app, ["user:create"], input="Grace\ngrace@example.com\nhunter2-x\n")
    assert result.exit_code == 0, result.output

    record = fake_repo.created[0]
    assert record["name"] == "Grace"
    assert record["email"] == "grace@example.com"
    assert record["password"] == "hunter2-x"

    plain = ANSI_RE.sub("", result.output)
    assert "Name" in plain and "Email" in plain  # the prompts appeared
    assert "hunter2-x" not in plain  # hidden prompt — never echoed


def test_user_create_rejects_invalid_email(project, fake_repo):
    result = runner.invoke(
        cli_app,
        ["user:create", "--name", "X", "--email", "not-an-email", "--password", "pw-123456"],
    )
    assert result.exit_code == 1
    assert fake_repo.created == []


def test_user_create_rejects_empty_password(project, fake_repo):
    result = runner.invoke(cli_app, ["user:create"], input="X\nx@y.z\n\n")
    assert result.exit_code == 1
    assert fake_repo.created == []


def test_user_create_admin_flag_grants_the_flag(project, fake_repo):
    # The promotion path: --admin must reach the repository as is_admin=True.
    result = runner.invoke(
        cli_app,
        [
            "user:create",
            "--name",
            "Grace",
            "--email",
            "grace@example.com",
            "--password",
            "s3cret-pass",
            "--admin",
        ],
    )
    assert result.exit_code == 0, result.output
    assert fake_repo.created[0]["is_admin"] is True


def test_user_create_without_admin_flag_stays_regular(project, fake_repo):
    result = runner.invoke(
        cli_app,
        [
            "user:create",
            "--name",
            "Grace",
            "--email",
            "grace@example.com",
            "--password",
            "s3cret-pass",
        ],
    )
    assert result.exit_code == 0, result.output
    assert fake_repo.created[0]["is_admin"] is False


def test_user_create_without_accounts_module_exits_one(project, monkeypatch):
    import fastplace.cli.provisioning as provisioning

    def _missing():
        raise ImportError("No module named 'app.modules.accounts'")

    monkeypatch.setattr(provisioning, "_accounts_repository", _missing)

    result = runner.invoke(
        cli_app, ["user:create", "--name", "X", "--email", "x@y.z", "--password", "pw-123456"]
    )
    assert result.exit_code == 1
    plain = ANSI_RE.sub("", result.output)
    assert "accounts" in plain
    assert "make:auth" in plain  # the fix is named


# ---------------------------------------------------------------------------
# user:create end-to-end — a tmp project with the make:auth accounts module
# ---------------------------------------------------------------------------


@pytest.fixture()
def accounts_project(tmp_path, monkeypatch):
    """A minimal tmp project carrying the scaffolded accounts module."""
    from fastplace.cli.auth_scaffold import _USER_MODEL_TEMPLATE, _USER_REPOSITORY_TEMPLATE

    (tmp_path / "asgi.py").write_text("# marker — the _project_root() check\n")
    for package in (
        "app",
        "app/modules",
        "app/modules/accounts",
        "app/modules/accounts/models",
        "app/modules/accounts/repositories",
    ):
        (tmp_path / package).mkdir(parents=True, exist_ok=True)
        (tmp_path / package / "__init__.py").write_text("")
    (tmp_path / "app/modules/accounts/models/user.py").write_text(_USER_MODEL_TEMPLATE)
    (tmp_path / "app/modules/accounts/repositories/user_repository.py").write_text(
        _USER_REPOSITORY_TEMPLATE
    )
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_user_create_end_to_end_through_real_repository(accounts_project):
    """The full path in a clean interpreter: project import, repository,
    Hash, database row. A subprocess is required — in the shared test
    process the repo's own ``users`` table is already registered on
    ``Model.metadata`` by earlier suites, so the tmp project's User would
    collide; real users run the CLI in a fresh process anyway.
    """
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "APP_ENV"}

    def run_snippet(code: str) -> subprocess.CompletedProcess[str]:
        # Fixed argv, the venv interpreter — never a shell string.
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=accounts_project,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )

    # Schema first: the tmp project's users table (`python -c` puts cwd on
    # sys.path, so the project's app package resolves).
    schema = run_snippet(
        "import asyncio; from fastplace.db import db; "
        "import app.modules.accounts.models.user; asyncio.run(db.create_all())"
    )
    assert schema.returncode == 0, schema.stderr

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from fastplace.cli import app; app()",
            "user:create",
            "--name",
            "Ada Lovelace",
            "--email",
            "Ada@Example.Test",
            "--password",
            "analytical-engine",
        ],
        cwd=accounts_project,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    plain = ANSI_RE.sub("", proc.stdout)
    assert "ada@example.test" in plain  # the email was normalized to lower case
    assert "analytical-engine" not in plain  # the password never prints

    verify = run_snippet(
        "import asyncio\n"
        "from app.modules.accounts.models.user import User\n"
        "async def main():\n"
        "    user = await User.where(User.email == 'ada@example.test').first()\n"
        "    assert user is not None\n"
        "    print(user.password_hash)\n"
        "asyncio.run(main())"
    )
    assert verify.returncode == 0, verify.stderr

    from fastplace.auth.hashing import Hash

    assert Hash.check("analytical-engine", verify.stdout.strip())


def test_user_create_admin_flag_end_to_end(accounts_project):
    """--admin through the real repository: the stored row carries is_admin."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "APP_ENV"}

    def run_snippet(code: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=accounts_project,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )

    schema = run_snippet(
        "import asyncio; from fastplace.db import db; "
        "import app.modules.accounts.models.user; asyncio.run(db.create_all())"
    )
    assert schema.returncode == 0, schema.stderr

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from fastplace.cli import app; app()",
            "user:create",
            "--name",
            "Grace Hopper",
            "--email",
            "grace@example.test",
            "--password",
            "compiler-pioneer",
            "--admin",
        ],
        cwd=accounts_project,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout

    verify = run_snippet(
        "import asyncio\n"
        "from app.modules.accounts.models.user import User\n"
        "async def main():\n"
        "    user = await User.where(User.email == 'grace@example.test').first()\n"
        "    assert user is not None\n"
        "    print('IS_ADMIN', user.is_admin)\n"
        "asyncio.run(main())"
    )
    assert verify.returncode == 0, verify.stderr
    assert "IS_ADMIN True" in verify.stdout
