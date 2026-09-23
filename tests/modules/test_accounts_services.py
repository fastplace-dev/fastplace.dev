"""T8 — accounts repository + auth/registration services (spec §4.4/§4.5)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fastplace.auth.guards import SessionGuard
from fastplace.auth.providers import OrmUserProvider
from fastplace.cache import reset_cache
from fastplace.errors import ValidationError


@pytest.fixture(autouse=True)
def _fresh_db(_fresh_app_modules, monkeypatch, tmp_path):
    # Explicit dependency on the conftest purge so the per-test DATABASE_URL is
    # set before anything (re-)imports the app modules and their models.
    from fastplace.db import reset_db

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    yield
    reset_db()


@pytest.fixture(autouse=True)
def _clean_listeners():
    from fastplace.events import reset_listeners

    reset_listeners()
    yield
    reset_listeners()


@pytest.fixture(autouse=True)
def _fresh_cache():
    # The guard's login limiter counts into the default cache — keep the
    # counters test-local so failures in one test never lock out another.
    reset_cache()
    yield
    reset_cache()


@pytest.fixture(autouse=True)
def _guard_uses_orm_provider(_fresh_app_modules, monkeypatch):
    # guard() reads config-driven provider settings; in tests point it at the
    # local User model with the same OrmUserProvider the app uses. Imported
    # after the conftest purge so the patch lands on the live module objects,
    # and patched on BOTH services — each imports ``guard`` into its own
    # module namespace, so patching auth_service alone would leave the
    # registration service on the config-driven guard.
    import app.modules.accounts.services.auth_service as auth_service_module
    import app.modules.accounts.services.registration_service as registration_service_module
    from app.modules.accounts.models.user import User

    def _orm_guard(name=None):
        return SessionGuard(OrmUserProvider(User))

    monkeypatch.setattr(auth_service_module, "guard", _orm_guard)
    monkeypatch.setattr(registration_service_module, "guard", _orm_guard)


@pytest.fixture()
async def accounts(_fresh_db, _guard_uses_orm_provider):
    """Fresh post-purge app classes with the users table created.

    The conftest purge deletes every ``app.*`` module per test, so the classes
    must be imported here (never at module scope) and the schema created after
    the fresh User class registers its table.
    """
    from app.modules.accounts.models.user import User
    from app.modules.accounts.repositories.user_repository import UserRepository
    from app.modules.accounts.services.auth_service import AuthService
    from app.modules.accounts.services.registration_service import RegistrationService
    from fastplace.db import db

    await db.create_all()
    return SimpleNamespace(
        User=User,
        UserRepository=UserRepository,
        AuthService=AuthService,
        RegistrationService=RegistrationService,
    )


def make_request() -> SimpleNamespace:
    return SimpleNamespace(session={}, scope={}, cookies={}, user=None, ip="127.0.0.1")


class TestUserRepository:
    async def test_find_by_email_returns_the_user(self, accounts):
        UserRepository = accounts.UserRepository
        await UserRepository().create_user(
            name="Firoz", email="firoz@example.test", password="secret123"
        )
        found = await UserRepository().find_by_email("firoz@example.test")
        assert found is not None and found.name == "Firoz"

    async def test_find_by_email_is_none_for_unknown_email(self, accounts):
        assert await accounts.UserRepository().find_by_email("nobody@example.test") is None


class TestRegistrationService:
    async def test_register_creates_the_user_and_logs_them_in(self, accounts):
        RegistrationService = accounts.RegistrationService
        registered: list[dict] = []
        from fastplace.events import listen

        listen("Registered", lambda e: registered.append(dict(e.payload)))
        request = make_request()

        user = await RegistrationService().register(
            request,
            {
                "name": "Firoz",
                "email": "Firoz@Example.test",
                "password": "secret123",
                "password_confirmation": "secret123",
            },
        )

        assert user.id is not None
        assert user.email == "firoz@example.test"  # normalized to lowercase
        from fastplace.auth.hashing import Hash

        assert Hash.check("secret123", user.password_hash) is True
        assert request.session["user_id"] == user.id  # logged in
        assert registered == [{"user_id": user.id, "email": "firoz@example.test"}]

    async def test_register_rejects_a_duplicate_email(self, accounts):
        UserRepository = accounts.UserRepository
        await UserRepository().create_user(
            name="First", email="firoz@example.test", password="secret123"
        )
        with pytest.raises(ValidationError) as exc_info:
            await accounts.RegistrationService().register(
                make_request(),
                {
                    "name": "Second",
                    "email": "firoz@example.test",
                    "password": "secret123",
                    "password_confirmation": "secret123",
                },
            )
        assert "email" in exc_info.value.errors

    async def test_register_rejects_a_short_password(self, accounts):
        with pytest.raises(ValidationError) as exc_info:
            await accounts.RegistrationService().register(
                make_request(),
                {
                    "name": "Firoz",
                    "email": "firoz@example.test",
                    "password": "short",
                    "password_confirmation": "short",
                },
            )
        assert "password" in exc_info.value.errors

    async def test_register_rejects_a_confirmation_mismatch(self, accounts):
        with pytest.raises(ValidationError) as exc_info:
            await accounts.RegistrationService().register(
                make_request(),
                {
                    "name": "Firoz",
                    "email": "firoz@example.test",
                    "password": "secret123",
                    "password_confirmation": "different",
                },
            )
        assert exc_info.value.errors["password"] == ["The password confirmation does not match."]


class TestAuthService:
    async def test_login_attempts_and_succeeds(self, accounts):
        await accounts.UserRepository().create_user(
            name="Firoz", email="firoz@example.test", password="secret123"
        )
        request = make_request()

        await accounts.AuthService().login(
            request, {"email": "firoz@example.test", "password": "secret123"}
        )

        assert request.session["user_id"] is not None

    async def test_login_failure_raises_the_422_credentials_contract(self, accounts):
        await accounts.UserRepository().create_user(
            name="Firoz", email="firoz@example.test", password="secret123"
        )
        with pytest.raises(ValidationError) as exc_info:
            await accounts.AuthService().login(
                make_request(),
                {"email": "firoz@example.test", "password": "wrong-pass"},
            )
        assert exc_info.value.errors == {"email": ["These credentials do not match our records."]}
