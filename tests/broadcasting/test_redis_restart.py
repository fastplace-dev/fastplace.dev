"""Redis driver listener recovery — fakes, no broker needed.

The integration lane (``test_redis_driver.py``) skips wherever no local
redis exists, so CI never exercises crash recovery. These tests pin the
restart bookkeeping — broker-side re-subscription and displaced-resource
release — against fakes, which is exactly where the regression lived: a
rebuilt listener that issues no SUBSCRIBE ever again.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from fastplace.broadcasting_redis import RedisBroadcastBus


class FakePubSub:
    def __init__(self) -> None:
        self.subscribed = False
        self.subscribe_calls: list[tuple[str, ...]] = []
        self.unsubscribe_calls: list[tuple[str, ...]] = []
        self.closed = False

    async def subscribe(self, *keys: str) -> None:
        self.subscribe_calls.append(keys)
        self.subscribed = True

    async def unsubscribe(self, *keys: str) -> None:
        self.unsubscribe_calls.append(keys)

    async def get_message(
        self, ignore_subscribe_messages: bool = True, timeout: float = 0.25
    ) -> None:
        await asyncio.sleep(0.01)
        return None

    async def aclose(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self) -> None:
        self.pubsub_obj = FakePubSub()
        self.publishes: list[tuple[str, str]] = []
        self.closed = False

    def pubsub(self) -> FakePubSub:
        return self.pubsub_obj

    async def publish(self, key: str, data: str) -> None:
        self.publishes.append((key, data))

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_clients(monkeypatch: pytest.MonkeyPatch) -> list[FakeClient]:
    """Patch ``Redis.from_url`` so the bus builds fakes; record every one."""
    made: list[FakeClient] = []

    def _from_url(url: str, **kwargs: Any) -> FakeClient:
        client = FakeClient()
        made.append(client)
        return client

    monkeypatch.setattr("redis.asyncio.Redis.from_url", staticmethod(_from_url))
    return made


async def _drain(bus: RedisBroadcastBus) -> None:
    """Let the listener loop run its sync/message cycles once."""
    for _ in range(6):
        await asyncio.sleep(0.02)
    assert bus._task is not None


async def test_crashed_listener_resubscribes_on_restart(fake_clients: list[FakeClient]) -> None:
    bus = RedisBroadcastBus(url="redis://unused", prefix="fftest:")
    received: list[str] = []
    bus.subscribe("chan", lambda channel, data: received.append(data))
    await bus.start()
    await _drain(bus)
    first = fake_clients[0].pubsub_obj
    assert ("fftest:chan",) in first.subscribe_calls  # broker side established

    # Crash the listener the way a broker loss does: the task dies while
    # the client/pubsub pair stays referenced (not the clean close() path).
    task = bus._task
    assert task is not None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    await bus.start()
    await _drain(bus)
    assert len(fake_clients) == 2  # a fresh pair was built
    assert ("fftest:chan",) in fake_clients[1].pubsub_obj.subscribe_calls
    # The displaced pair is released, not leaked until GC.
    assert fake_clients[0].closed or first.closed


async def test_crashed_listener_recovers_delivery(fake_clients: list[FakeClient]) -> None:
    bus = RedisBroadcastBus(url="redis://unused", prefix="fftest:")
    received: list[str] = []
    bus.subscribe("chan", lambda channel, data: received.append(data))
    await bus.start()
    await _drain(bus)

    task = bus._task
    assert task is not None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # The recovery trigger from real life: the next publish restarts the
    # listener — and with it the broker-side subscription.
    await bus.publish("chan", "hello")
    await _drain(bus)
    assert ("fftest:chan", "hello") in fake_clients[-1].publishes
    assert ("fftest:chan",) in fake_clients[-1].pubsub_obj.subscribe_calls


async def test_close_during_teardown_does_not_resurrect(fake_clients: list[FakeClient]) -> None:
    bus = RedisBroadcastBus(url="redis://unused", prefix="fftest:")
    bus.subscribe("chan", lambda channel, data: None)
    await bus.start()
    await _drain(bus)
    assert len(fake_clients) == 1

    close_task = asyncio.create_task(bus.close())
    # A publish racing shutdown must not spawn a second client pair that
    # close() would never tear down (zombie listener + leaked connection).
    await asyncio.sleep(0)
    try:
        await bus.publish("chan", "racing")
    except Exception:
        pass  # a clean refusal is acceptable; a resurrection is not
    await close_task
    made_after_close = len(fake_clients)
    await asyncio.sleep(0.05)
    assert len(fake_clients) == made_after_close
