"""Query scopes — reusable, chainable query fragments (blueprint §8).

Two flavors share this module: chainable scopes (explicit ``.query().…()``
fragments) and **global scopes** — criteria applied to *every* query on a
model unless explicitly removed. The core soft-delete filter is one global
scope in the registry (blueprint Query Scopes table: "Global — automatic
(core)"); the same extension point is where a package such as
fastplace-tenancy contributes the ``company`` scope.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class GlobalScope:
    """One automatic filter for every query on a model.

    ``criteria(model)`` returns SQL criteria (empty list = no-op). Register
    with ``Model.add_global_scope(name, scope)``; escape per-query with
    ``.without_global_scope(name)``. The name ``"soft_delete"`` is reserved
    for the core scope behind ``with_deleted()`` / ``only_deleted()``.
    """

    def criteria(self, model: Any) -> list[Any]:
        raise NotImplementedError


class SoftDeleteScope(GlobalScope):
    """The core global scope: default queries exclude soft-deleted rows."""

    def criteria(self, model: Any) -> list[Any]:
        if "deleted_at" not in model.__table__.columns:
            return []
        return [model.deleted_at.is_(None)]


class scope:  # noqa: A001 — deliberate public decorator name
    """Mark a classmethod as a chainable query scope.

    Usage::

        class Project(Model):
            @scope
            def in_progress(cls, query):
                return query.where(cls.status == "in_progress")

        projects = await Project.query().in_progress().get()
    """

    _is_scope = True

    def __init__(self, fn: Callable) -> None:
        self.fn = fn
        self.__name__ = getattr(fn, "__name__", "scope")
        self.__doc__ = getattr(fn, "__doc__", None)

    def __get__(self, obj: Any, objtype: Any | None = None) -> Any:
        # Behave like a bound classmethod so `Model.scope_name` is callable.
        import functools

        return functools.partial(self.fn, objtype)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        return self.fn(*args, **kwargs)


def is_scope(attr: Any) -> bool:
    """True when a class attribute is a scope."""
    return getattr(attr, "_is_scope", False) is True
