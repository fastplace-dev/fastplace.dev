"""Provider credential contract: retrieve/validate/remember/rehash (spec §4.3)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fastplace.auth.hashing import Hash
from fastplace.auth.providers import DictUserProvider


def user_with(password_hash: str) -> SimpleNamespace:
    return SimpleNamespace(id=7, email="firoz@example.test", password_hash=password_hash)


class TestDictProviderCredentials:
    async def test_retrieve_matches_scalar_conditions(self):
        provider = DictUserProvider()
        provider.add(user_with(Hash.make("secret")))
        found = await provider.retrieve_by_credentials({"email": "firoz@example.test"})
        assert found is not None
        assert found.id == 7

    async def test_password_is_never_a_lookup_key(self):
        provider = DictUserProvider()
        provider.add(user_with(Hash.make("secret")))
        assert await provider.retrieve_by_credentials({"password": "secret"}) is None

    async def test_unknown_credentials_return_none(self):
        provider = DictUserProvider()
        provider.add(user_with(Hash.make("secret")))
        assert await provider.retrieve_by_credentials({"email": "nobody@example.test"}) is None

    async def test_validate_credentials_checks_hash_only(self):
        provider = DictUserProvider()
        user = user_with(Hash.make("secret"))
        assert await provider.validate_credentials(user, {"password": "secret"}) is True
        assert await provider.validate_credentials(user, {"password": "wrong"}) is False

    async def test_validate_false_without_stored_hash_or_supply(self):
        provider = DictUserProvider()
        assert (
            await provider.validate_credentials(SimpleNamespace(id=1), {"password": "x"}) is False
        )
        assert await provider.validate_credentials(user_with(Hash.make("x")), {}) is False

    async def test_rehash_rewrites_when_the_hasher_wants_it(self, monkeypatch):
        monkeypatch.setattr(Hash, "needs_rehash", classmethod(lambda cls, hashed: True))
        provider = DictUserProvider()
        user = user_with("$legacy$digest")
        assert await provider.rehash_password_if_required(user, {"password": "secret"}) is True
        assert Hash.check("secret", user.password_hash)

    async def test_rehash_skipped_when_current(self, monkeypatch):
        monkeypatch.setattr(Hash, "needs_rehash", classmethod(lambda cls, hashed: False))
        provider = DictUserProvider()
        user = user_with(Hash.make("secret"))
        assert await provider.rehash_password_if_required(user, {"password": "secret"}) is False

    async def test_force_rewrites_even_when_current(self):
        provider = DictUserProvider()
        user = user_with(Hash.make("secret"))
        before = user.password_hash
        assert (
            await provider.rehash_password_if_required(user, {"password": "secret"}, force=True)
            is True
        )
        assert user.password_hash != before
        assert Hash.check("secret", user.password_hash)

    async def test_update_remember_token_sets_the_hash(self):
        provider = DictUserProvider()
        user = user_with("x")
        await provider.update_remember_token(user, "sha256-digest")
        assert user.remember_token == "sha256-digest"


# --- ORM provider (same in-fixture model pattern as test_orm_provider.py) ---


@pytest.fixture(autouse=True)
def _reset_db(monkeypatch, tmp_path):
    from fastplace.db import reset_db

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/providers.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    yield
    reset_db()


def _inline_model() -> type:
    """A per-test model on the shared base with unique class/table names.

    The shared declarative base outlives every test, so a repeated
    "CredentialUser" would collide with the earlier definition — and
    clearing the shared registry to dodge that would strand every model
    module another test file already imported.
    """
    import uuid

    from fastplace.orm import Field, Model

    tag = uuid.uuid4().hex[:8]
    return type(
        f"CredentialUser{tag}",
        (Model,),
        {
            "__tablename__": f"auth_credential_users_{tag}",
            "__annotations__": {"id": int, "email": str, "password_hash": str},
            "id": Field(primary_key=True),
            "email": Field(unique=True),
            "password_hash": Field(default=""),
        },
    )


async def test_the_db_reset_never_clears_the_shared_model_registry(monkeypatch):
    """Engine reset only — same invariant as test_orm_provider.py pins."""
    from fastplace.auth.providers import OrmUserProvider
    from fastplace.db import db
    from fastplace.orm.model import Model

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("shared model registry must never be cleared here")

    monkeypatch.setattr(Model.metadata, "clear", _forbidden)
    monkeypatch.setattr("sqlalchemy.orm.clear_mappers", _forbidden)

    model = _inline_model()
    await db.create_all()
    user = await model.create(email="firoz@example.test", password_hash="x")
    try:
        assert await OrmUserProvider(model).resolve(user.id) is not None
    finally:
        Model.metadata.remove(model.__table__)


@pytest.fixture()
async def credential_model():
    from fastplace.db import db
    from fastplace.orm.model import Model

    model = _inline_model()

    await db.create_all()
    try:
        yield model
    finally:
        # Take only our own table back off the shared metadata — the
        # throwaway class must not surface in later autogenerate scopes.
        Model.metadata.remove(model.__table__)


class TestOrmProviderCredentials:
    async def test_retrieve_by_column_lookup(self, credential_model):
        from fastplace.auth.providers import OrmUserProvider

        model = credential_model
        await model.create(email="firoz@example.test", password_hash=Hash.make("secret"))
        provider = OrmUserProvider(model)
        found = await provider.retrieve_by_credentials({"email": "firoz@example.test"})
        assert found is not None
        assert found.email == "firoz@example.test"

    async def test_all_scalar_conditions_must_match(self, credential_model):
        from fastplace.auth.providers import OrmUserProvider

        model = credential_model
        await model.create(email="firoz@example.test", password_hash=Hash.make("secret"))
        provider = OrmUserProvider(model)
        # Non-column keys are skipped (EC4), so the "all conditions must
        # match" intent is pinned with two column keys — the wrong id wins.
        assert (
            await provider.retrieve_by_credentials({"email": "firoz@example.test", "id": 424242})
            is None
        )

    async def test_unknown_column_returns_none_not_an_error(self, credential_model):
        from fastplace.auth.providers import OrmUserProvider

        await credential_model.create(email="firoz@example.test", password_hash=Hash.make("secret"))
        provider = OrmUserProvider(credential_model)
        # A payload whose keys name no column matches nobody — even with
        # rows present (EC4: never fall back to an unconditioned lookup).
        assert await provider.retrieve_by_credentials({"nickname": "fz"}) is None

    async def test_password_only_credentials_return_none(self, credential_model):
        from fastplace.auth.providers import OrmUserProvider

        provider = OrmUserProvider(credential_model)
        assert await provider.retrieve_by_credentials({"password": "secret"}) is None

    async def test_validate_and_rehash_on_a_real_row(self, credential_model):
        from fastplace.auth.providers import OrmUserProvider

        model = credential_model
        user = await model.create(email="v@example.test", password_hash="$legacy$digest")
        provider = OrmUserProvider(model)
        assert await provider.validate_credentials(user, {"password": "secret"}) is False
        changed = await provider.rehash_password_if_required(
            user, {"password": "secret"}, force=True
        )
        assert changed is True
        fresh = await model.find(user.id)
        assert fresh is not None
        assert Hash.check("secret", fresh.password_hash)  # persisted via save()
