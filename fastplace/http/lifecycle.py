"""Fastplace lifecycle hooks — registered here, executed on the ASGI lifespan."""

from __future__ import annotations

import inspect
from typing import Callable

_startup_hooks: list[Callable] = []
_shutdown_hooks: list[Callable] = []


def on_startup(fn: Callable) -> Callable:
    """Register a coroutine (or callable) to run on application startup."""
    _startup_hooks.append(fn)
    return fn


def on_shutdown(fn: Callable) -> Callable:
    """Register a coroutine (or callable) to run on application shutdown."""
    _shutdown_hooks.append(fn)
    return fn


async def run_startup() -> None:
    for hook in list(_startup_hooks):
        if inspect.iscoroutinefunction(hook):
            await hook()
        else:
            hook()


async def run_shutdown() -> None:
    for hook in list(_shutdown_hooks):
        if inspect.iscoroutinefunction(hook):
            await hook()
        else:
            hook()


def reset() -> None:
    """Clear all hooks — used by tests to isolate lifecycle state."""
    _startup_hooks.clear()
    _shutdown_hooks.clear()
