"""Domain events → broadcasting bridge — the opt-in ``broadcast_events`` map.

The bridge is explicit per event: only names registered through
:func:`broadcast_events` ever reach a channel, and a mapped event rides the
same post-commit buffer the queue leg uses (a rolled-back write never
happened, so its broadcasts must not either). Nothing here needs a broker —
the memory bus proves delivery.
"""

from __future__ import annotations

import pytest

from fastplace.broadcasting import broadcast_events, reset_broadcasting
from fastplace.events import DomainEvent, dispatch


@pytest.fixture(autouse=True)
def _clean_broadcasting():
    reset_broadcasting()
    yield
    reset_broadcasting()


class _Recorder:
    """Subscribe-everything spy around the memory bus."""

    def __init__(self):
        self.frames: list[tuple[str, object]] = []

    async def deliver(self, channel: str, data: str):
        import json

        self.frames.append((channel, json.loads(data)))

    def channels(self) -> list[str]:
        return [channel for channel, _ in self.frames]


@pytest.fixture()
def recorder():
    return _Recorder()


class TestBridge:
    async def test_mapped_event_broadcasts_its_payload(self, recorder):
        from fastplace.broadcasting import broadcast_bus

        broadcast_events({"order_created": "orders.{order_id}"})
        broadcast_bus().subscribe("orders.9", recorder.deliver)
        await dispatch(DomainEvent("order_created", {"order_id": 9, "n": 1}))
        assert recorder.frames == [("orders.9", {"order_id": 9, "n": 1})]

    async def test_unmapped_event_never_broadcasts(self, recorder):
        from fastplace.broadcasting import broadcast_bus

        broadcast_events({"order_created": "orders.{order_id}"})
        broadcast_bus().subscribe("orders.9", recorder.deliver)
        await dispatch(DomainEvent("order_shipped", {"order_id": 9}))
        assert recorder.frames == []

    async def test_template_without_placeholders_is_a_constant_channel(self, recorder):
        from fastplace.broadcasting import broadcast_bus

        broadcast_events({"pulse": "health"})
        broadcast_bus().subscribe("health", recorder.deliver)
        await dispatch(DomainEvent("pulse", {"beat": 1}))
        assert recorder.channels() == ["health"]

    async def test_missing_placeholder_fails_loud_naming_the_event(self):
        broadcast_events({"order_created": "orders.{order_id}"})
        with pytest.raises(Exception) as excinfo:
            await dispatch(DomainEvent("order_created", {"id": 9}))
        assert "order_created" in str(excinfo.value)

    async def test_mapped_event_alone_does_not_warn_about_dead_listeners(self, recorder, caplog):
        import logging

        broadcast_events({"order_created": "orders.{order_id}"})
        with caplog.at_level(logging.WARNING, logger="fastplace.events"):
            await dispatch(DomainEvent("order_created", {"order_id": 9}))
        assert not [r for r in caplog.records if "no listener" in r.getMessage()]


class TestPostCommitBuffer:
    """Mapped broadcasts ride the transaction buffer like the queue leg."""

    async def test_inside_a_transaction_the_broadcast_waits_for_commit(self, recorder, monkeypatch):
        from fastplace.broadcasting import broadcast_bus
        from fastplace.db import reset_db

        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
        monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
        reset_db()
        from fastplace.db import db

        try:
            broadcast_events({"order_created": "orders.{order_id}"})
            broadcast_bus().subscribe("orders.9", recorder.deliver)
            async with db.transaction():
                await dispatch(DomainEvent("order_created", {"order_id": 9}))
                assert recorder.frames == []  # nothing before the commit
            assert recorder.channels() == ["orders.9"]  # everything after
        finally:
            await db.dispose()

    async def test_rollback_discards_the_buffered_broadcast(self, recorder, monkeypatch):
        from fastplace.broadcasting import broadcast_bus
        from fastplace.db import reset_db

        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
        monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
        reset_db()
        from fastplace.db import db

        try:
            broadcast_events({"order_created": "orders.{order_id}"})
            broadcast_bus().subscribe("orders.9", recorder.deliver)
            with pytest.raises(RuntimeError):
                async with db.transaction():
                    await dispatch(DomainEvent("order_created", {"order_id": 9}))
                    raise RuntimeError("boom")
            assert recorder.frames == []
            # The bridge stays armed — the next healthy dispatch broadcasts.
            await dispatch(DomainEvent("order_created", {"order_id": 9}))
            assert recorder.channels() == ["orders.9"]
        finally:
            await db.dispose()
