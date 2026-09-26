"""First real /register signup becomes the admin (binding product ruling).

Admin is NEVER seeded or hardcoded: the registration service grants it by
signup-count, the column is not mass-assignable, and no admin email literal
ships anywhere. These tests exercise the scaffold TEMPLATES (what
``fastplace new --auth`` writes), materialized into a tmp project.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from fastplace.cli.auth_scaffold import (
    _PASSWORD_POLICY_TEMPLATE,
    _REGISTRATION_SERVICE_TEMPLATE,
    _USER_MODEL_TEMPLATE,
    _USER_REPOSITORY_TEMPLATE,
)


@pytest.fixture()
def accounts_project(tmp_path, monkeypatch):
    """A tmp project carrying the scaffolded accounts module (model, repo,
    policy, registration service) — the pieces first-admin touches."""
    (tmp_path / "asgi.py").write_text("# marker — the _project_root() check\n")
    for package in (
        "app",
        "app/modules",
        "app/modules/accounts",
        "app/modules/accounts/models",
        "app/modules/accounts/repositories",
        "app/modules/accounts/services",
    ):
        (tmp_path / package).mkdir(parents=True, exist_ok=True)
        (tmp_path / package / "__init__.py").write_text("")
    (tmp_path / "app/modules/accounts/models/user.py").write_text(_USER_MODEL_TEMPLATE)
    (tmp_path / "app/modules/accounts/repositories/user_repository.py").write_text(
        _USER_REPOSITORY_TEMPLATE
    )
    (tmp_path / "app/modules/accounts/services/password_policy.py").write_text(
        _PASSWORD_POLICY_TEMPLATE
    )
    (tmp_path / "app/modules/accounts/services/registration_service.py").write_text(
        _REGISTRATION_SERVICE_TEMPLATE
    )
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(accounts_project, code: str) -> subprocess.CompletedProcess[str]:
    """A subprocess is required — in the shared test process the repo's own
    ``users`` table is already registered on ``Model.metadata``, so the tmp
    project's User would collide (same pattern as test_provisioning.py)."""
    env = {k: v for k, v in os.environ.items() if k != "APP_ENV"}
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=accounts_project,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


_SCHEMA = (
    "import asyncio; from fastplace.db import db; "
    "import app.modules.accounts.models.user; asyncio.run(db.create_all())"
)

# guard().login needs a live session backend; first-admin does not depend on
# it, so the snippet swaps the module's guard for a no-op stand-in.
_REGISTER = """
import asyncio
from types import SimpleNamespace

import app.modules.accounts.services.registration_service as rs

class _NoopGuard:
    async def login(self, request, user):
        pass

rs.guard = lambda: _NoopGuard()

async def main():
    request = SimpleNamespace(session={{}}, scope={{}}, cookies={{}}, user=None, ip="127.0.0.1")
    payload = {{"name": "{name}", "email": "{email}", "password": "{password}",
               "password_confirmation": "{password}"}}
    user = await rs.RegistrationService().register(request, payload)
    print("IS_ADMIN", user.is_admin)

asyncio.run(main())
"""


def test_first_registration_is_admin(accounts_project):
    assert _run(accounts_project, _SCHEMA).returncode == 0
    proc = _run(
        accounts_project,
        _REGISTER.format(name="Ada", email="ada@example.com", password="hunter2-x"),
    )
    assert proc.returncode == 0, proc.stderr
    assert "IS_ADMIN True" in proc.stdout


def test_second_registration_is_regular(accounts_project):
    assert _run(accounts_project, _SCHEMA).returncode == 0
    first = _run(
        accounts_project,
        _REGISTER.format(name="Ada", email="ada@example.com", password="hunter2-x"),
    )
    assert first.returncode == 0, first.stderr
    second = _run(
        accounts_project,
        _REGISTER.format(name="Bob", email="bob@example.com", password="hunter2-x"),
    )
    assert second.returncode == 0, second.stderr
    assert "IS_ADMIN False" in second.stdout


def test_is_admin_is_never_mass_assignable(accounts_project):
    # OWASP mass-assignment privilege escalation: a crafted User.create()
    # payload carrying is_admin=True must silently drop the flag.
    assert _run(accounts_project, _SCHEMA).returncode == 0
    proc = _run(
        accounts_project,
        "import asyncio\n"
        "from app.modules.accounts.models.user import User\n"
        "async def main():\n"
        "    user = await User.create(name='Eve', email='eve@example.com',"
        " password_hash='x', is_admin=True)\n"
        "    print('IS_ADMIN', user.is_admin)\n"
        "asyncio.run(main())",
    )
    assert proc.returncode == 0, proc.stderr
    assert "IS_ADMIN False" in proc.stdout
