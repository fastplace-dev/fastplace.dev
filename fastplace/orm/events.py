"""Model lifecycle events (blueprint §8): creating, created, updating, updated,
deleting, deleted, restored."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

EVENTS = (
    "creating",
    "created",
    "updating",
    "updated",
    "deleting",
    "deleted",
    "restored",
)


class EventRegistry:
    """Per-model event handler storage, inherited through the MRO."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = {}

    def on(self, event: str, handler: Callable) -> None:
        if event not in EVENTS:
            raise ValueError(f"Unknown model event '{event}'. Valid: {EVENTS}")
        self._handlers.setdefault(event, []).append(handler)

    def handlers_for(self, event: str) -> list[Callable]:
        return list(self._handlers.get(event, []))


async def fire(model: Any, event: str) -> None:
    """Fire an event on the model's class chain; aborts the operation on error."""
    for klass in type(model).__mro__:
        registry: EventRegistry | None = klass.__dict__.get("_fastplace_events")
        if registry is None:
            continue
        for handler in registry.handlers_for(event):
            if inspect.iscoroutinefunction(handler):
                await handler(model)
            else:
                handler(model)

    # Domain-event bridge (blueprint §8): ``__dispatches__`` maps lifecycle
    # names to domain event names — the Model Event → Domain Event → Queue
    # path for cross-module side effects. The payload stays serializable.
    dispatches = getattr(type(model), "__dispatches__", None)
    if dispatches and event in dispatches:
        from fastplace.events import DomainEvent, dispatch

        pk_name = type(model)._pk_attr().key
        payload = {"model": type(model).__name__, "id": getattr(model, pk_name, None)}
        await dispatch(DomainEvent(dispatches[event], payload))
