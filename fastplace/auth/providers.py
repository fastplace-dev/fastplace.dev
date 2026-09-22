"""User providers — how guards turn a stored identifier back into a user.

A provider owns two things: extracting the identifier from a user object
(:meth:`UserProvider.identifier`) and resolving that identifier back to a
user (:meth:`UserProvider.resolve`). Guards stay storage-agnostic; the
provider is where "users live in the ORM / in a dict / somewhere else"
belongs (dependency inversion — guards depend on the abstraction).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any, Protocol

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import NoInspectionAvailable

_MISSING = object()


def user_identifier(user: Any) -> Any:
    """Extract the durable identifier of a user object.

    ORM models use their primary key (persisted identity); anything else
    falls back to an ``id`` attribute, and finally to the object itself
    (plain values used as their own identity).
    """
    try:
        state = sa_inspect(user)
    except NoInspectionAvailable:
        return getattr(user, "id", user)
    identity = state.identity
    if identity is None:  # not yet persisted
        return getattr(user, "id", None)
    return identity[0] if len(identity) == 1 else tuple(identity)


def _credential_conditions(credentials: dict[str, Any]) -> dict[str, Any]:
    """Scalar, non-password entries become lookup conditions (spec §4.2).

    Password keys are validation material, never lookup keys; non-scalars
    (callables, lists) are ignored — ORM-side composition stays Phase 2.
    """
    return {
        key: value
        for key, value in credentials.items()
        if key not in ("password", "password_hash") and isinstance(value, (str, int, float, bool))
    }


class _PasswordMixin:
    """Password-column plumbing shared by the built-in providers (spec §4.3)."""

    #: Attribute checked first for the stored digest; ``password`` is the fallback.
    PASSWORD_COLUMN = "password_hash"

    def _stored_password(self, user: Any) -> tuple[str, str] | None:
        for column in (self.PASSWORD_COLUMN, "password"):
            digest = getattr(user, column, None)
            if isinstance(digest, str) and digest:
                return column, digest
        return None

    @staticmethod
    def _supplied_password(credentials: dict[str, Any]) -> str | None:
        for key in ("password", "password_hash"):
            value = credentials.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    async def validate_credentials(self, user: Any, credentials: dict[str, Any]) -> bool:
        """Hash.check only — never a lookup (spec §4.3)."""
        from fastplace.auth.hashing import Hash

        stored = self._stored_password(user)
        supplied = self._supplied_password(credentials)
        if stored is None or supplied is None:
            return False
        return Hash.check(supplied, stored[1])

    async def rehash_password_if_required(
        self, user: Any, credentials: dict[str, Any], *, force: bool = False
    ) -> bool:
        """Upgrade a legacy digest when the active hasher changed (spec §4.3).

        Returns True when the stored hash was rewritten. ORM users persist
        via ``save()`` when available; plain objects keep the attribute
        assignment only.
        """
        from fastplace.auth.hashing import Hash

        stored = self._stored_password(user)
        supplied = self._supplied_password(credentials)
        if stored is None or supplied is None:
            return False
        if not force and not Hash.needs_rehash(stored[1]):
            return False
        setattr(user, stored[0], Hash.make(supplied))
        save = getattr(user, "save", None)
        if save is not None:
            await save()
        return True


class UserProvider(Protocol):
    """Storage strategy behind the auth guards."""

    def identifier(self, user: Any) -> Any: ...

    async def resolve(self, identifier: Any) -> Any | None: ...

    async def retrieve_by_credentials(self, credentials: dict[str, Any]) -> Any | None: ...

    async def validate_credentials(self, user: Any, credentials: dict[str, Any]) -> bool: ...

    async def update_remember_token(self, user: Any, token_hash: str) -> None: ...

    async def rehash_password_if_required(
        self, user: Any, credentials: dict[str, Any], *, force: bool = False
    ) -> bool: ...


class DictUserProvider(_PasswordMixin):
    """In-memory provider — tests, seeds, and apps without a user model.

    A single shared instance (:data:`dict_provider`) backs the default
    ``users`` provider in ``config/auth.py`` so registrations made at
    startup (seeders, tests) survive guard construction.
    """

    def __init__(self) -> None:
        self._users: dict[Any, Any] = {}

    def add(self, user: Any) -> None:
        self._users[self.identifier(user)] = user

    def identifier(self, user: Any) -> Any:
        return user_identifier(user)

    async def resolve(self, identifier: Any) -> Any | None:
        return self._users.get(identifier)

    async def retrieve_by_credentials(self, credentials: dict[str, Any]) -> Any | None:
        """Lookup only — the dict provider scans its in-memory users."""
        conditions = _credential_conditions(credentials)
        if not conditions:
            return None  # a password-only dict must never match "some user"
        for user in self._users.values():
            if all(getattr(user, key, None) == value for key, value in conditions.items()):
                return user
        return None

    async def update_remember_token(self, user: Any, token_hash: str) -> None:
        # No schema to persist into — carry the hash on the object so custom
        # flows can read it back within the process.
        user.remember_token = token_hash


#: Process-wide default provider instance (see :class:`DictUserProvider`).
dict_provider = DictUserProvider()


class OrmUserProvider(_PasswordMixin):
    """Resolves users through the Fastplace ORM by primary key.

    ``model`` is a dotted path (``app.modules.accounts.models.user.User``)
    imported lazily so the auth layer never boots the ORM at import time,
    or a model class / async-find callable for direct wiring in tests.
    """

    def __init__(self, model: str | type | Callable) -> None:
        self._model_ref = model
        self._model: Any = _MISSING

    @property
    def model(self) -> Any:
        if self._model is _MISSING:
            if isinstance(self._model_ref, str):
                module_path, _, attr = self._model_ref.rpartition(".")
                self._model = getattr(importlib.import_module(module_path), attr)
            else:
                self._model = self._model_ref
        return self._model

    def identifier(self, user: Any) -> Any:
        return user_identifier(user)

    async def resolve(self, identifier: Any) -> Any | None:
        find = getattr(self.model, "find", None)
        if find is None:
            raise TypeError(f"OrmUserProvider model {self.model!r} has no async find(pk) API.")
        return await find(identifier)

    async def retrieve_by_credentials(self, credentials: dict[str, Any]) -> Any | None:
        """Column-based lookup honoring every scalar condition (spec §4.3).

        Soft-deleted users are deliberately unresolvable — the global scope
        rides Model.query(); reset flows that must reach them use
        with_deleted() explicitly (documented deviation, spec §4.3).

        Payload keys that are not table columns (a "remember" flag, a model
        @property) are ignored — they never reach a WHERE clause (EC4 hard
        gate) — and a payload whose keys name no column at all matches
        nobody.
        """
        conditions = _credential_conditions(credentials)
        conditions = {
            key: value for key, value in conditions.items() if key in self.model.__table__.columns
        }
        if not conditions:
            return None
        query = self.model.query()
        for key, value in conditions.items():
            query = query.where(getattr(self.model, key) == value)
        return await query.first()

    async def update_remember_token(self, user: Any, token_hash: str) -> None:
        user.remember_token = token_hash
        save = getattr(user, "save", None)
        if save is not None:
            await save()


def provider_from_config(config_get: Callable) -> UserProvider:
    """Build the configured user provider (``AUTH_USER_PROVIDER`` in config/auth.py)."""
    name = config_get("AUTH_USER_PROVIDER", "users") or "users"
    providers = config_get("AUTH_PROVIDERS", {}) or {}
    entry = providers.get(name, {}) or {}
    driver = entry.get("driver", "dict")
    if driver == "dict":
        return dict_provider
    if driver == "orm":
        model = entry.get("model")
        if not model:
            raise ValueError(f"Provider '{name}' driver 'orm' needs a 'model' dotted path.")
        return OrmUserProvider(model)
    raise ValueError(f"Unknown user provider driver '{driver}' (provider '{name}').")
