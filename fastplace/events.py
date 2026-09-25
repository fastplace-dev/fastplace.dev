"""Domain events — cross-module side effects without cross-module calls.

Blueprint §8: ``Model Event -> Domain Event -> Queue / WebSocket /
Notification / AI``. A lifecycle handler stays local to its model; work that
must reach another module (notifications, AI ingestion, search indexing) is
dispatched as a :class:`DomainEvent` instead — boundary rule 4 of the
modular-monolith section.

    from fastplace.events import DomainEvent, dispatch, listen

    listen("project_created", indexer.refresh)          # in-process listener
    await dispatch(DomainEvent("project_created", {"project_id": 7}))

Models usually wire this through ``__dispatches__`` (see
``fastplace.orm.events.fire``): the payload is ``{"model": <class name>,
"id": <primary key>}`` — always serializable, never the ORM instance.

Queue bridging: when a ``@Job`` is registered under the event's name,
``dispatch`` also enqueues the payload for a worker (``to_queue=None`` — the
auto default). ``to_queue=True`` requires the job; ``to_queue=False`` keeps
the dispatch in-process only.

Ordering contract: inside ``db.transaction()`` the queue leg is *buffered on
the transaction scope* and flushed only after the commit — a worker cannot
see an uncommitted row, and a rollback discards the buffered events (a
rolled-back write never happened, so its side effects must not either).
In-process listeners still run immediately: they share the transaction and
belong to the operation, exactly like lifecycle handlers. Outside a
commit-owning scope (``Model.create``/``update``/``delete`` commit per write
before firing), the enqueue happens inline — after that write's commit. The
auto (``to_queue=None``) enqueue never fails the originating write on a
broker outage: it logs and gives up. The explicit ``to_queue=True`` path
stays strict.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DomainEvent",
    "listen",
    "dispatch",
    "registered_listeners",
    "reset_listeners",
]

logger = logging.getLogger("fastplace.events")


@dataclass(frozen=True)
class DomainEvent:
    """One named thing that happened, carrying a JSON-safe payload."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)


_listeners: dict[str, list[Callable[[DomainEvent], Any]]] = {}


def listen(name: str, handler: Callable[[DomainEvent], Any]) -> None:
    """Register an in-process listener for an event name."""
    _listeners.setdefault(name, []).append(handler)


async def dispatch(event: DomainEvent, *, to_queue: bool | None = None) -> None:
    """Deliver ``event`` to listeners and (optionally) the queue.

    Listener exceptions propagate — a listener is part of the operation, the
    same contract lifecycle handlers already follow.
    """
    ran_listener = False
    for handler in _listeners.get(event.name, []):
        ran_listener = True
        if inspect.iscoroutinefunction(handler):
            await handler(event)
        else:
            handler(event)

    if to_queue is False:
        return

    from fastplace.queue import jobs

    has_job = event.name in jobs()
    if not ran_listener and not has_job and to_queue is None:
        logger.warning(
            "domain event %r dispatched with no listener and no registered job — "
            "cross-module work may be silently skipped (did this process import app/jobs?)",
            event.name,
        )
    if to_queue is not True and not has_job:
        return

    # Inside a commit-owning scope the enqueue waits for the commit (see the
    # module docstring); otherwise it runs inline.
    from fastplace.orm.session import ambient

    state = ambient()
    if state is not None and state.owns_commit:
        state.deferred_domain_events.append((event.name, dict(event.payload), to_queue))
        return

    await _enqueue(event.name, event.payload, to_queue)


async def _enqueue(name: str, payload: dict[str, Any], to_queue: bool | None) -> None:
    """Enqueue one event; only the strict explicit path may raise."""
    from fastplace.queue import queue

    try:
        await queue().dispatch(name, **payload)
    except Exception:
        if to_queue is True:
            raise
        # Auto-enqueue after a committed write: the write is durable — a
        # broker outage must not turn it into a client-visible error.
        logger.exception("domain event %r lost: queue dispatch failed", name)


async def flush_deferred_domain_events(state: Any) -> None:
    """Enqueue the events buffered on a committed transaction scope."""
    buffered, state.deferred_domain_events = state.deferred_domain_events, []
    for name, payload, to_queue in buffered:
        await _enqueue(name, payload, to_queue)


def discard_deferred_domain_events(state: Any) -> None:
    """Drop the events buffered on a rolled-back transaction scope."""
    state.deferred_domain_events = []


def reset_listeners() -> None:
    """Clear all listeners — test isolation."""
    _listeners.clear()


def registered_listeners() -> dict[str, list[str]]:
    """A copy of the listener registry: event name -> sorted handler names.

    Handler names are ``module.qualname`` where Python knows them, so the
    CLI (and tooling) can show which callable answers an event without
    touching the private registry or holding live references.
    """

    def _name(handler: Callable[[DomainEvent], Any]) -> str:
        qualname = getattr(handler, "__qualname__", None)
        if qualname is None:
            return repr(handler)
        module = getattr(handler, "__module__", "") or "?"
        return f"{module}.{qualname}"

    return {
        name: sorted(_name(handler) for handler in handlers)
        for name, handlers in _listeners.items()
    }
