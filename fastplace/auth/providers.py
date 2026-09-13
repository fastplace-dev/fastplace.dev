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


class UserProvider(Protocol):
    """Storage strategy behind the auth guards."""

    def identifier(self, user: Any) -> Any: ...

    async def resolve(self, identifier: Any) -> Any | None: ...


class DictUserProvider:
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


#: Process-wide default provider instance (see :class:`DictUserProvider`).
dict_provider = DictUserProvider()


class OrmUserProvider:
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
