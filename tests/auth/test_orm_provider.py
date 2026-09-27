"""OrmUserProvider — guards resolving users through the Fastplace ORM.

The `orm` driver of AUTH_PROVIDERS (config/auth.py) was shipped unexercised;
these tests prove the whole path: model resolution from a dotted path,
identifier extraction, and `find(pk)` round-trips.
"""

from __future__ import annotations

import uuid

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
def _reset_db():
    from fastplace.db import reset_db

    reset_db()
    yield
    reset_db()


def _inline_model(name: str, table: str) -> type[Model]:
    """A per-test model on the shared base with unique class/table names.

    The shared declarative base outlives every test, so a repeated
    "AuthUser"/"auth_users" would collide with the earlier definition —
    and clearing the shared registry to dodge that would strand every
    model module another test file already imported.
    """
    tag = uuid.uuid4().hex[:8]
    return type(
        f"{name}{tag}",
        (Model,),
        {
            "__tablename__": f"{table}_{tag}",
            "__annotations__": {"id": int, "email": str},
            "id": Field(primary_key=True),
        },
    )


async def test_the_db_reset_never_clears_the_shared_model_registry(
    db_url, account_model, monkeypatch
):
    """The autouse reset disposes engines only. Clearing the shared
    declarative metadata or the global mapper registry strands every model
    module another test file already imported — downstream tests (CLI model
    introspection, in-process scaffolds) then see phantom models or table
    collisions for the rest of the session."""

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("shared model registry must never be cleared here")

    monkeypatch.setattr(Model.metadata, "clear", _forbidden)
    monkeypatch.setattr("sqlalchemy.orm.clear_mappers", _forbidden)

    user = await account_model.create(email="firoz@example.test")
    assert await OrmUserProvider(account_model).resolve(user.id) is not None


@pytest.fixture()
async def account_model(db_url):
    model = _inline_model("AuthUser", "auth_users")

    from fastplace.db import db

    await db.create_all()
    try:
        yield model
    finally:
        # Take only our own table back off the shared metadata — the
        # throwaway class must not surface in later autogenerate scopes.
        Model.metadata.remove(model.__table__)


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
    # A unique table name: this module executes against the shared base,
    # and the test may run more than once in one session.
    table = f"lazy_members_{uuid.uuid4().hex[:8]}"
    (tmp_path / "accounts_pkg" / "models.py").write_text(
        "from fastplace.orm import Field, Model\n\n"
        "class Member(Model):\n"
        f"    __tablename__ = '{table}'\n"
        "    id: int = Field(primary_key=True)\n"
        "    email: str\n"
    )
    import sys

    monkeypatch.syspath_prepend(str(tmp_path))

    provider = OrmUserProvider("accounts_pkg.models.Member")
    assert provider.model.__name__ == "Member"
    del sys.modules["accounts_pkg.models"]
    del sys.modules["accounts_pkg"]
    # Same courtesy: our throwaway table leaves the shared metadata again.
    from fastplace.orm.model import Model

    Model.metadata.remove(Model.metadata.tables[table])


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


async def test_retrieve_by_credentials_skips_non_column_attributes(account_model):
    # attempt() forwards the whole request payload as credentials — keys that
    # are not table columns (a "remember" flag, a model @property) must be
    # ignored, not turned into WHERE conditions.
    account_model.is_admin = property(lambda self: True)  # attr, not a column
    user = await account_model.create(email="firoz@example.test")

    provider = OrmUserProvider(account_model)
    found = await provider.retrieve_by_credentials(
        {"email": "firoz@example.test", "is_admin": True, "remember": "on"}
    )

    assert found is not None
    assert found.id == user.id
