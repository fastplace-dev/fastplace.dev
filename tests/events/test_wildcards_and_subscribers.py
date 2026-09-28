"""Wildcard listeners and subscribers on the domain-event registry.

The exact-name registry stays the primary path; wildcards (`fnmatch`
patterns) fan one handler out across an event family, and subscribers
group a family's handlers into one class. Dispatch order is a contract:
exact listeners in registration order, then wildcards in registration
order.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import pytest

from fastplace.events import (
    DomainEvent,
    Subscriber,
    dispatch,
    listen,
    registered_listeners,
    reset_listeners,
    subscribe,
)


def _noop(event: DomainEvent) -> None:
    """A handler that does nothing — registry-shape assertions only."""


@pytest.fixture(autouse=True)
def _isolated():
    from fastplace.queue import reset_queue, reset_registry

    reset_listeners()
    reset_registry()
    reset_queue()
    yield
    reset_listeners()
    reset_registry()
    reset_queue()


async def test_wildcard_listener_runs_on_matching_event():
    seen: list[DomainEvent] = []
    listen("order.*", seen.append)

    await dispatch(DomainEvent("order.created", {"id": 7}))

    assert [e.name for e in seen] == ["order.created"]
    assert seen[0].payload == {"id": 7}


async def test_wildcard_does_not_run_on_a_non_matching_event():
    seen: list[DomainEvent] = []
    listen("invoice.*", seen.append)

    await dispatch(DomainEvent("order.created", {}))

    assert seen == []


async def test_exact_listeners_run_before_wildcards():
    """Ordering is a contract: exact-name listeners first (registration
    order), then wildcards (registration order) — regardless of when the
    wildcard was registered."""
    order: list[str] = []
    listen("order.*", lambda e: order.append("wild-first"))
    listen("order.created", lambda e: order.append("exact-a"))
    listen("order.created", lambda e: order.append("exact-b"))
    listen("order.*", lambda e: order.append("wild-second"))

    await dispatch(DomainEvent("order.created", {}))

    assert order == ["exact-a", "exact-b", "wild-first", "wild-second"]


async def test_registration_composition_runs_once_per_match():
    """Pinned semantics: registration is per-pattern, not per-callable — a
    handler matching several patterns runs once per match (exact + wildcard
    = twice for a name both answer), and the same callable registered twice
    under one name runs twice. Changing either must be a conscious
    dedup decision, not an accident."""
    calls: list[str] = []

    def handler(event: DomainEvent) -> None:
        calls.append(event.name)

    listen("order.created", handler)
    listen("order.*", handler)
    listen("order.created", handler)  # same callable, same name, twice

    await dispatch(DomainEvent("order.created", {}))

    assert calls == ["order.created", "order.created", "order.created"]


async def test_wildcard_matches_across_dots():
    """`fnmatch` semantics: `*` crosses dots, `?` is one char."""
    seen: list[DomainEvent] = []
    listen("order.*", seen.append)

    await dispatch(DomainEvent("order.paid.cancelled", {}))

    assert [e.name for e in seen] == ["order.paid.cancelled"]

    single: list[DomainEvent] = []
    listen("user.?", single.append)
    await dispatch(DomainEvent("user.created", {}))
    await dispatch(DomainEvent("user.a", {}))
    # `?` is exactly one character — `user.created` (7 chars) must not match.
    assert [e.name for e in single] == ["user.a"]


async def test_wildcard_matches_a_bare_dotless_name():
    """`*` alone is the catch-all: even a bare name without a dot matches."""
    seen: list[DomainEvent] = []
    listen("*", seen.append)

    await dispatch(DomainEvent("ping", {}))

    assert [e.name for e in seen] == ["ping"]


async def test_mid_segment_wildcard_fills_one_slot():
    seen: list[DomainEvent] = []
    listen("user.*.created", seen.append)

    await dispatch(DomainEvent("user.a.created", {}))
    await dispatch(DomainEvent("user.created", {}))  # no middle segment — no match

    assert [e.name for e in seen] == ["user.a.created"]


async def test_wildcard_listener_exceptions_propagate():
    """A wildcard listener is part of the operation — the same contract as
    exact listeners and lifecycle handlers."""

    def boom(event: DomainEvent) -> None:
        raise RuntimeError("wildcard listener failed")

    listen("order.*", boom)

    with pytest.raises(RuntimeError, match="wildcard listener failed"):
        await dispatch(DomainEvent("order.created", {}))


async def test_sync_and_async_wildcard_handlers():
    seen: list[str] = []

    def sync_handler(event: DomainEvent) -> None:
        seen.append("sync")

    async def async_handler(event: DomainEvent) -> None:
        seen.append("async")

    listen("order.*", sync_handler)
    listen("order.*", async_handler)

    await dispatch(DomainEvent("order.created", {}))

    assert seen == ["sync", "async"]


class AwaitableListener:
    """A callable object whose ``__call__`` is async — calling it returns an
    awaitable without the callable itself being a coroutine function (a
    Mock/AsyncMock behaves the same way)."""

    def __init__(self) -> None:
        self.seen: list[DomainEvent] = []

    async def __call__(self, event: DomainEvent) -> None:
        self.seen.append(event)


async def test_callable_object_with_async_call_is_awaited():
    """A listener whose *call* returns an awaitable must be awaited —
    recognizing only coroutine functions would silently never run it."""
    listener = AwaitableListener()
    listen("order.created", listener)

    await dispatch(DomainEvent("order.created", {"id": 3}))

    assert [e.name for e in listener.seen] == ["order.created"]
    assert listener.seen[0].payload == {"id": 3}


async def test_wildcard_listener_alone_suppresses_the_no_consumer_warning(caplog):
    """A wildcard match counts as a consumer — the mis-wiring warning is for
    events nobody answers."""
    listen("order.*", lambda e: None)

    with caplog.at_level(logging.WARNING, logger="fastplace.events"):
        await dispatch(DomainEvent("order.created", {"x": 1}))

    assert not any("order.created" in r.message for r in caplog.records)


async def test_unmatched_name_still_warns_when_only_wildcards_exist(caplog):
    with caplog.at_level(logging.WARNING, logger="fastplace.events"):
        await dispatch(DomainEvent("invoice.created", {"x": 1}))

    assert any("invoice.created" in r.message for r in caplog.records)


async def test_queue_bridging_is_unchanged_by_wildcard_listeners():
    """The queue leg keys on the exact event name only; a wildcard listener
    must not change what gets enqueued — just whether the warning fires."""
    from fastplace.queue import Job, MemoryQueue, queue

    calls: list[int] = []

    @Job()
    async def order_created(id: int):  # pragma: no cover — test double
        calls.append(id)

    listen("order.*", lambda e: None)
    await dispatch(DomainEvent("order_created", {"id": 5}))

    memory = queue()
    assert isinstance(memory, MemoryQueue)
    assert len(memory.pending) == 1
    await memory.run_pending()
    assert calls == [5]


async def test_matching_wildcard_listener_still_bridges_a_matching_event_to_the_queue():
    """A wildcard listener that actually matches the dispatched event is a
    listener leg — the queue leg still fires for the exact-name job."""
    from fastplace.queue import Job, MemoryQueue, queue

    calls: list[int] = []

    @Job(name="order.paid")
    async def order_paid(id: int):  # pragma: no cover — test double
        calls.append(id)

    listen("order.*", lambda e: None)
    await dispatch(DomainEvent("order.paid", {"id": 9}), to_queue=True)

    memory = queue()
    assert isinstance(memory, MemoryQueue)
    assert len(memory.pending) == 1
    await memory.run_pending()
    assert calls == [9]


async def test_to_queue_false_stays_in_process_even_with_a_wildcard_match():
    from fastplace.queue import queue

    listen("order.*", lambda e: None)
    await dispatch(DomainEvent("order.created", {}), to_queue=False)

    assert not getattr(queue(), "pending", [])


class BillingSubscriber(Subscriber):
    """One class answering an event family — exact and wildcard together."""

    def __init__(self) -> None:
        self.recorded: list[DomainEvent] = []
        self.audited: list[DomainEvent] = []

    def listening(self) -> dict[str, Callable[[DomainEvent], Any]]:
        return {
            "invoice_created": self.record,
            "order.*": self.audit,
        }

    def record(self, event: DomainEvent) -> None:
        self.recorded.append(event)

    async def audit(self, event: DomainEvent) -> None:
        self.audited.append(event)


async def test_subscriber_registers_exact_and_wildcard_handlers():
    billing = BillingSubscriber()
    subscribe(billing)

    await dispatch(DomainEvent("invoice_created", {"id": 1}))
    await dispatch(DomainEvent("order.created", {"id": 2}))

    assert [e.name for e in billing.recorded] == ["invoice_created"]
    assert [e.name for e in billing.audited] == ["order.created"]


async def test_subscriber_bound_methods_run_through_the_registry():
    billing = BillingSubscriber()
    subscribe(billing)

    names = registered_listeners()
    assert "invoice_created" in names
    assert "order.*" in names
    assert any(BillingSubscriber.record.__qualname__ in h for h in names["invoice_created"])
    assert any(BillingSubscriber.audit.__qualname__ in h for h in names["order.*"])


def test_registered_listeners_includes_wildcard_entries():
    """Wildcard registrations stay under their pattern — the key carries a
    wildcard metacharacter, which is the marker."""
    listen("order.created", lambda e: None)
    listen("order.*", lambda e: None)

    names = registered_listeners()

    assert set(names) == {"order.created", "order.*"}
    assert names["order.created"]  # exact entries keep their shape
    assert names["order.*"]


def test_reset_listeners_clears_wildcards_and_subscriber_registrations():
    billing = BillingSubscriber()
    subscribe(billing)
    listen("misc.*", lambda e: None)

    reset_listeners()

    assert registered_listeners() == {}


async def test_subscriber_with_empty_mapping_is_a_no_op():
    class Empty(Subscriber):
        def listening(self) -> dict[str, Callable[[DomainEvent], Any]]:
            return {}

    subscribe(Empty())

    assert registered_listeners() == {}


def test_subscribe_rejects_a_non_mapping_from_listening():
    class Bad(Subscriber):
        def listening(self) -> Any:  # deliberately lies — runtime must catch it
            return ["not-a-mapping"]

    with pytest.raises(TypeError, match="mapping"):
        subscribe(Bad())


def test_listen_rejects_a_non_string_name():
    with pytest.raises(TypeError, match="event name"):
        listen(123, _noop)  # type: ignore[arg-type]
