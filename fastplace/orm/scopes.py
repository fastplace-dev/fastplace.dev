"""Query scopes — reusable, chainable query fragments (blueprint §8)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


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
