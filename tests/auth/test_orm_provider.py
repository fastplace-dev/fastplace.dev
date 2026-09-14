"""OrmUserProvider — guards resolving users through the Fastplace ORM.

The `orm` driver of AUTH_PROVIDERS (config/auth.py) was shipped unexercised;
these tests prove the whole path: model resolution from a dotted path,
identifier extraction, and `find(pk)` round-trips.
"""

from __future__ import annotations

import pytest

from fastplace.auth.providers import OrmUserProvider, provider_from_config
from fastplace.orm import Field, Model


@pytest.fixture()
def db_url(monkeypatch: pytest.MonkeyPatch) -> str:
    url = "sqlite+aiosqlite:///:memory:"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    return url


@pytest.fixture(autouse=True)
def _reset_db_and_registry():
    from fastplace.db import reset_db

    reset_db()
    yield
    reset_db()
    from fastplace.orm.model import Model

    Model.metadata.clear()
    from sqlalchemy.orm import clear_mappers

    clear_mappers()


@pytest.fixture()
async def account_model(db_url):
    class AuthUser(Model):
        __tablename__ = "auth_users"

        id: int = Field(primary_key=True)
        email: str

    from fastplace.db import db

    await db.create_all()
    return AuthUser


async def test_identifier_extracts_the_primary_key(account_model):
    user = await account_model.create(email="firoz@example.test")
    provider = OrmUserProvider(account_model)
    assert provider.identifier(user) == user.id


def test_identifier_for_unpersisted_instance_falls_back_to_id(account_model):
    transient = account_model(id=99, email="new@example.test")
    provider = OrmUserProvider(account_model)
    assert provider.identifier(transient) == 99


async def test_resolve_round_trips_through_find(account_model):
    user = await account_model.create(email="firoz@example.test")
    provider = OrmUserProvider(account_model)

    resolved = await provider.resolve(user.id)
    assert resolved is not None
    assert resolved.email == "firoz@example.test"


async def test_resolve_returns_none_for_unknown_ids(account_model):
    provider = OrmUserProvider(account_model)
    assert await provider.resolve(424242) is None


def test_dotted_model_path_imports_lazily(tmp_path, monkeypatch):
    (tmp_path / "accounts_pkg").mkdir()
    (tmp_path / "accounts_pkg" / "__init__.py").write_text("")
    (tmp_path / "accounts_pkg" / "models.py").write_text(
        "from fastplace.orm import Field, Model\n\n"
        "class Member(Model):\n"
        "    __tablename__ = 'lazy_members'\n"
        "    id: int = Field(primary_key=True)\n"
        "    email: str\n"
    )
    import sys

    monkeypatch.syspath_prepend(str(tmp_path))

    provider = OrmUserProvider("accounts_pkg.models.Member")
    assert provider.model.__name__ == "Member"
    del sys.modules["accounts_pkg.models"]
    del sys.modules["accounts_pkg"]


def test_provider_from_config_builds_the_orm_driver():
    provider = provider_from_config(
        lambda key, default=None: {
            "AUTH_USER_PROVIDER": "users",
            "AUTH_PROVIDERS": {"users": {"driver": "orm", "model": "x.y.Z"}},
        }.get(key, default)
    )
    assert isinstance(provider, OrmUserProvider)


def test_provider_from_config_orm_driver_requires_a_model():
    with pytest.raises(ValueError, match="model"):
        provider_from_config(
            lambda key, default=None: {
                "AUTH_USER_PROVIDER": "users",
                "AUTH_PROVIDERS": {"users": {"driver": "orm"}},
            }.get(key, default)
        )


async def test_model_without_find_api_fails_loudly():
    class NotAModel:
        pass

    provider = OrmUserProvider(NotAModel)
    with pytest.raises(TypeError, match="find"):
        await provider.resolve(1)
