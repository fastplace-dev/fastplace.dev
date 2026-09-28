"""The Fastplace testing toolkit — import surface for app test suites.

The pytest fixtures live in :mod:`fastplace.testing.plugin` (auto-loaded);
this package re-exports the toolkit classes with lazy imports so the plugin
stays cheap to load for every pytest run.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "Clock",
    "FakeEvents",
    "FakeMail",
    "FakeQueue",
    "ModelFactory",
    "PushedJob",
    "TestClient",
    "TestResponse",
]

_EXPORTS = {
    "TestResponse": ("fastplace.testing.client", "TestResponse"),
    "TestClient": ("fastplace.testing.client", "TestClient"),
    "FakeMail": ("fastplace.testing.mail", "FakeMail"),
    "FakeQueue": ("fastplace.testing.queue", "FakeQueue"),
    "PushedJob": ("fastplace.testing.queue", "PushedJob"),
    "FakeEvents": ("fastplace.testing.events", "FakeEvents"),
    "Clock": ("fastplace.testing.clock", "Clock"),
    "ModelFactory": ("fastplace.testing.factories", "ModelFactory"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    import importlib

    return getattr(importlib.import_module(module_name), attr)


def __dir__() -> list[str]:
    return sorted(list(globals()) + list(_EXPORTS))
