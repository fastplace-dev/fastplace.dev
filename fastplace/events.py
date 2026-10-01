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

Listeners answer exact event names or ``fnmatch`` wildcard patterns —
``listen("order.*", handler)`` answers the whole ``order.`` family. A name
without a wildcard metacharacter (``*``, ``?``, ``[``) registers exactly;
anything else registers as a pattern. Dispatch order is a contract: exact
listeners in registration order first, then wildcard listeners in
registration order. For grouped wiring, a :class:`Subscriber` returns its
whole family's mapping from ``listening()`` and one ``subscribe(...)``
call registers it through the same registry::

    class BillingSubscriber(Subscriber):
        def listening(self):
            return {"invoice_created": self.record, "order.*": self.audit}

    subscribe(BillingSubscriber())

Queue bridging: when a ``@Job`` is registered under the event's name,
``dispatch`` also enqueues the payload for a worker (``to_queue=None`` — the
auto default). ``to_queue=True`` requires the job; ``to_queue=False`` keeps
the dispatch in-process only. The queue leg always keys on the exact event
name — wildcards only widen who listens in-process.

Broadcast bridging: an event name mapped through
``fastplace.broadcasting.broadcast_events`` also publishes on its mapped
channel. The mapping is explicit per event — unmapped names are never
broadcast — and the broadcast leg rides the same post-commit buffer as the
queue leg.

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

import fnmatch
import inspect
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DomainEvent",
    "Subscriber",
    "listen",
    "subscribe",
    "dispatch",
    "registered_listeners",
    "reset_listeners",
]

logger = logging.getLogger("fastplace.events")


# fnmatch's metacharacters — a name carrying any of these registers as a
# pattern instead of an exact listener.
_WILDCARD_CHARS = frozenset("*?[")


@dataclass(frozen=True)
class DomainEvent:
    """One named thing that happened, carrying a JSON-safe payload."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)


Listener = Callable[[DomainEvent], Any]


_listeners: dict[str, list[Listener]] = {}
# Patterns in registration order, translated up front so dispatch pays a
# regex match, not fnmatch's cache lookup, per wildcard listener.
_wildcards: list[tuple[str, re.Pattern[str], Listener]] = []


def listen(name: str, handler: Listener) -> None:
    """Register an in-process listener for an event name or wildcard pattern.

    ``order.created`` registers exactly; ``order.*`` registers as an
    ``fnmatch`` pattern (``*`` crosses dots, ``?`` is one character). Exact
    listeners always run before wildcards; a listener is part of the
    operation, so its exceptions propagate to the dispatcher. Registrations
    compose per pattern, not per callable: a handler matching several
    patterns runs once per match, and the same callable registered twice
    runs twice.
    """
    if not isinstance(name, str):
        raise TypeError(f"event name must be a str, got {type(name).__name__}")
    if _WILDCARD_CHARS & set(name):
        _wildcards.append((name, re.compile(fnmatch.translate(name)), handler))
    else:
        _listeners.setdefault(name, []).append(handler)


class Subscriber:
    """Base class for grouped event handlers.

    A subclass returns its wiring from :meth:`listening` — event name or
    wildcard pattern -> bound method — and one ``subscribe(instance)`` call
    registers the whole group through the same registry exact listeners use.
    Plain object wiring, no metaclass: the app constructs the instance, so
    tests and DI can hand-substitute a fake.
    """

    def listening(self) -> dict[str, Listener]:
        """Map event names (or ``fnmatch`` patterns) to bound methods."""
        return {}


def subscribe(subscriber: Subscriber) -> None:
    """Register every entry of ``subscriber.listening()`` via :func:`listen`.

    Composition follows :func:`listen`: a handler matching several patterns
    runs once per match, and a subscriber subscribed twice runs its
    handlers twice.
    """
    mapping = subscriber.listening()
    if not isinstance(mapping, Mapping):
        raise TypeError(
            f"{type(subscriber).__name__}.listening() must return a mapping of "
            f"event name -> handler, got {type(mapping).__name__}"
        )
    for name, handler in mapping.items():
        listen(name, handler)


async def dispatch(event: DomainEvent, *, to_queue: bool | None = None) -> None:
    """Deliver ``event`` to listeners and (optionally) the queue.

    Exact-name listeners run first in registration order, then wildcard
    listeners in registration order. Listener exceptions propagate — a
    listener is part of the operation, the same contract lifecycle handlers
    already follow.
    """
    ran_listener = False
    for handler in _listeners.get(event.name, []):
        ran_listener = True
        await _invoke(handler, event)
    for _pattern, matcher, handler in _wildcards:
        if not matcher.match(event.name):
            continue
        ran_listener = True
        await _invoke(handler, event)

    # Broadcast leg (opt-in map): rendered here — a template that does not
    # fit the payload is a wiring bug and fails loud, in-operation — but
    # published post-commit like the queue leg below.
    from fastplace.broadcasting import mapped_broadcast_channel

    channel = mapped_broadcast_channel(event.name, event.payload)

    # Both bridged legs obey the same ordering contract: inside a
    # commit-owning scope they buffer on the scope and land only after the
    # commit (a rollback discards them); outside one they run inline.
    from fastplace.orm.session import ambient

    state = ambient()
    scope = state if (state is not None and state.owns_commit) else None

    if to_queue is not False:
        from fastplace.queue import jobs

        has_job = event.name in jobs()
        if not ran_listener and not has_job and channel is None and to_queue is None:
            logger.warning(
                "domain event %r dispatched with no listener and no registered job — "
                "cross-module work may be silently skipped (did this process import app/jobs?)",
                event.name,
            )
        if to_queue is True or has_job:
            if scope is not None:
                scope.deferred_domain_events.append((event.name, dict(event.payload), to_queue))
            else:
                await _enqueue(event.name, event.payload, to_queue)

    if channel is not None:
        if scope is not None:
            scope.deferred_broadcasts.append((channel, dict(event.payload)))
        else:
            await _broadcast_leg_best_effort(channel, event.payload)


async def _broadcast_leg(channel: str | None, payload: dict[str, Any]) -> None:
    """Publish a mapped event now (the post-commit flush calls this too)."""
    if channel is None:
        return
    from fastplace.broadcasting import broadcast

    await broadcast(channel, payload)


async def _broadcast_leg_best_effort(channel: str | None, payload: dict[str, Any]) -> None:
    """Publish, or log and give up — the write that caused the broadcast is
    already durable, so a broker outage must not surface as a client-visible
    error (the queue leg's auto path follows the same rule)."""
    try:
        await _broadcast_leg(channel, payload)
    except Exception:
        logger.exception("broadcast on channel %r lost: broker dispatch failed", channel)


async def _invoke(handler: Listener, event: DomainEvent) -> None:
    """Run one listener; sync handlers run inline, awaitable results awaited.

    The awaitable check is on the *result*, not on the callable: a listener
    whose call returns an awaitable without itself being a coroutine
    function (a callable object with ``async def __call__``, a Mock) would
    otherwise silently never run.
    """
    result = handler(event)
    if inspect.isawaitable(result):
        await result


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
    broadcasts, state.deferred_broadcasts = state.deferred_broadcasts, []
    for channel, payload in broadcasts:
        # Same outage posture as the inline leg above — the write is already
        # durable, so a broker failure here is logged, never raised into the
        # caller of a committed transaction.
        await _broadcast_leg_best_effort(channel, payload)


def discard_deferred_domain_events(state: Any) -> None:
    """Drop the events buffered on a rolled-back transaction scope."""
    state.deferred_domain_events = []
    state.deferred_broadcasts = []


def reset_listeners() -> None:
    """Clear all listeners — exact, wildcard, and subscriber-registered."""
    _listeners.clear()
    _wildcards.clear()


def registered_listeners() -> dict[str, list[str]]:
    """A copy of the listener registry: event name -> sorted handler names.

    Exact registrations key on the event name; wildcard registrations key on
    their pattern — every pattern carries at least one wildcard
    metacharacter, which is the marker (``"order.*"`` is a pattern,
    ``"order_created"`` is a name). Handler names are ``module.qualname``
    where Python knows them, so the CLI (and tooling) can show which
    callable answers an event without touching the private registry or
    holding live references.
    """

    def _name(handler: Listener) -> str:
        qualname = getattr(handler, "__qualname__", None)
        if qualname is None:
            return repr(handler)
        module = getattr(handler, "__module__", "") or "?"
        return f"{module}.{qualname}"

    registry: dict[str, list[str]] = {
        name: sorted(_name(handler) for handler in handlers)
        for name, handlers in _listeners.items()
    }

    patterns: dict[str, list[str]] = {}
    for pattern, _matcher, handler in _wildcards:
        patterns.setdefault(pattern, []).append(_name(handler))
    registry.update({pattern: sorted(handlers) for pattern, handlers in sorted(patterns.items())})
    return registry
