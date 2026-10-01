"""Broadcast-leg outage posture — a down broker never fails the write.

Both broadcast legs (inline and the post-commit flush) are best-effort once
the originating write is durable: a broker outage logs and gives up, the
same rule the queue leg's auto path follows. Channel-template rendering
stays fail-loud — a misconfigured mapping is a programming error, not an
outage (see tests/broadcasting/test_event_bridge.py).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from fastplace.broadcasting import broadcast_events, reset_broadcasting
from fastplace.events import DomainEvent, dispatch, flush_deferred_domain_events


@pytest.fixture(autouse=True)
def _clean_broadcasting():
    reset_broadcasting()
    yield
    reset_broadcasting()


@pytest.fixture()
def broker_outage(monkeypatch):
    # events._broadcast_leg imports broadcast at call time, so patching the
    # broadcasting module attribute intercepts every leg.
    async def unreachable(channel: str, payload) -> None:
        raise ConnectionRefusedError("broker unreachable")

    monkeypatch.setattr("fastplace.broadcasting.broadcast", unreachable)


class TestBroadcastLegOutage:
    async def test_inline_broadcast_leg_logs_and_gives_up(self, broker_outage, caplog):
        broadcast_events({"order_created": "orders.{order_id}"})
        with caplog.at_level(logging.ERROR, logger="fastplace.events"):
            await dispatch(DomainEvent("order_created", {"order_id": 9}))
        assert any(
            "orders.9" in record.getMessage() and record.levelno == logging.ERROR
            for record in caplog.records
        )

    async def test_buffered_flush_broadcast_leg_logs_and_gives_up(self, broker_outage, caplog):
        state = SimpleNamespace(
            deferred_domain_events=[], deferred_broadcasts=[("orders.9", {"order_id": 9})]
        )
        with caplog.at_level(logging.ERROR, logger="fastplace.events"):
            await flush_deferred_domain_events(state)
        assert state.deferred_broadcasts == []  # flushed regardless of outcome
        assert any(
            "orders.9" in record.getMessage() and record.levelno == logging.ERROR
            for record in caplog.records
        )
