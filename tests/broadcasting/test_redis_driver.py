"""Redis broadcast driver — real pub/sub fan-out across bus instances.

Runs against a real local redis when one is reachable on ``localhost:6379``
DB 11 (the established repo integration lane: socket probe, skipif when
absent, ``fftest-`` namespaces, never FLUSH). CI has no redis on this lane —
the whole module skips there; the memory driver suite carries the protocol
contract everywhere else.
"""

from __future__ import annotations

import asyncio
import inspect
import socket
import time
import uuid
from collections.abc import Callable
from urllib.parse import urlparse

import pytest

REDIS_URL = "redis://localhost:6379/11"


def _redis_reachable(url: str, timeout: float = 0.5) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname, "6379"), timeout=timeout):
            return True
    except OSError:
        return False


def _ns() -> str:
    return f"fftest-bc-{uuid.uuid4().hex[:8]}"


def make_bus(prefix: str, **kwargs):
    from fastplace.broadcasting_redis import RedisBroadcastBus

    return RedisBroadcastBus(url=REDIS_URL, prefix=prefix, **kwargs)


async def wait_for(predicate: Callable[[], object], timeout: float = 3.0) -> bool:
    """Poll until ``predicate`` (sync or async) is truthy or the budget dies."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return True
        await asyncio.sleep(0.02)
    return False


pytestmark = pytest.mark.skipif(
    not _redis_reachable(REDIS_URL), reason="no local redis on localhost:6379 — integration lane"
)


class TestRedisBusDelivery:
    async def test_publish_reaches_a_subscriber_on_another_bus(self):
        # The whole point of the redis driver: two bus instances (two
        # processes' worth of connections) fan out through the broker.
        prefix = f"fastplace:{_ns()}:"
        chan = "orders.42"
        receiver = make_bus(prefix)
        sender = make_bus(prefix)
        received: list[str] = []
        receiver.subscribe(chan, lambda channel, data: received.append(data))
        try:
            assert await wait_for(lambda: broker_knows(prefix, chan))
            await sender.publish(chan, '{"n":1}')
            assert await wait_for(lambda: bool(received))
            assert received == ['{"n":1}']
        finally:
            await receiver.close()
            await sender.close()

    async def test_payload_is_the_exact_string_no_reserialization(self):
        # Drivers move the serialized string; they never re-serialize. The
        # exact bytes published are the exact string delivered.
        prefix = f"fastplace:{_ns()}:"
        chan = "orders.42"
        exact = '{"a":1,"b":["x",true,null]}'
        receiver = make_bus(prefix)
        sender = make_bus(prefix)
        received: list[str] = []
        receiver.subscribe(chan, lambda channel, data: received.append(data))
        try:
            assert await wait_for(lambda: broker_knows(prefix, chan))
            await sender.publish(chan, exact)
            assert await wait_for(lambda: bool(received))
            assert received[0] == exact
        finally:
            await receiver.close()
            await sender.close()

    async def test_no_cross_channel_delivery(self):
        prefix = f"fastplace:{_ns()}:"
        receiver = make_bus(prefix)
        sender = make_bus(prefix)
        orders: list[str] = []
        invoices: list[str] = []
        receiver.subscribe("orders.42", lambda c, d: orders.append(d))
        receiver.subscribe("invoices.1", lambda c, d: invoices.append(d))
        try:
            assert await wait_for(lambda: broker_knows(prefix, "orders.42"))
            assert await wait_for(lambda: broker_knows(prefix, "invoices.1"))
            await sender.publish("orders.42", '{"n":1}')
            assert await wait_for(lambda: bool(orders))
            await asyncio.sleep(0.2)
            assert invoices == []
        finally:
            await receiver.close()
            await sender.close()

    async def test_subscriber_sees_its_own_channel_name(self):
        # The callback receives the *logical* channel (prefix stripped), so
        # socket-layer dispatch keys match what the app subscribed to.
        prefix = f"fastplace:{_ns()}:"
        chan = "company.7.private.orders.42"
        receiver = make_bus(prefix)
        sender = make_bus(prefix)
        seen: list[str] = []
        receiver.subscribe(chan, lambda channel, data: seen.append(channel))
        try:
            assert await wait_for(lambda: broker_knows(prefix, chan))
            await sender.publish(chan, '{"n":1}')
            assert await wait_for(lambda: bool(seen))
            assert seen == [chan]
        finally:
            await receiver.close()
            await sender.close()


class TestRedisPrefix:
    async def test_prefix_applies_at_publish_and_subscribe(self):
        # Every redis channel name hides behind BROADCAST_CHANNEL_PREFIX —
        # pub/sub is instance-global on the broker, and the prefix keeps
        # fastplace traffic out of every other app sharing the redis.
        # Proven by a raw third subscriber that never sees the logical name.
        import redis.asyncio as aioredis

        prefix = f"fastplace:{_ns()}:"
        chan = "orders.42"
        bus = make_bus(prefix)
        received: list[str] = []
        bus.subscribe(chan, lambda channel, data: received.append(data))

        raw = aioredis.Redis.from_url(REDIS_URL)
        pubsub = raw.pubsub()
        await pubsub.subscribe(f"{prefix}{chan}")
        try:
            assert await wait_for(lambda: broker_knows(prefix, chan))
            await bus.publish(chan, '{"proof":true}')

            async def raw_read() -> str | None:
                # First get_message after subscribe() consumes the broker's
                # subscribe ack (returns None immediately despite the
                # timeout) — loop until an actual message frame or budget.
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.25)
                    if message and message.get("type") == "message":
                        return message["data"].decode()
                return None

            assert await asyncio.wait_for(raw_read(), timeout=3.0) == '{"proof":true}'
            assert await wait_for(lambda: bool(received))
        finally:
            await pubsub.aclose()
            await raw.aclose()
            await bus.close()


class TestRedisSubscriptions:
    async def test_unsubscribe_stops_delivery(self):
        prefix = f"fastplace:{_ns()}:"
        chan = "orders.42"
        receiver = make_bus(prefix)
        sender = make_bus(prefix)
        received: list[str] = []
        unsub = receiver.subscribe(chan, lambda c, d: received.append(d))
        try:
            assert await wait_for(lambda: broker_knows(prefix, chan))
            await sender.publish(chan, '{"n":1}')
            assert await wait_for(lambda: len(received) == 1)
            unsub()
            # The broker-side subscription is gone too — the channel no
            # longer appears in the broker's active pubsub channel list
            # (the redis-side UNSUBSCRIBE rides the listener loop).
            assert await wait_for(lambda: broker_forgets(prefix, chan)), (
                "broker still lists the unsubscribed channel"
            )
            await sender.publish(chan, '{"n":2}')
            await asyncio.sleep(0.2)
            assert received == ['{"n":1}']
        finally:
            await receiver.close()
            await sender.close()

    async def test_close_stops_the_listener(self):
        prefix = f"fastplace:{_ns()}:"
        chan = "orders.42"
        receiver = make_bus(prefix)
        sender = make_bus(prefix)
        received: list[str] = []
        receiver.subscribe(chan, lambda c, d: received.append(d))
        try:
            assert await wait_for(lambda: broker_knows(prefix, chan))
            await sender.publish(chan, '{"n":1}')
            assert await wait_for(lambda: bool(received))
            task = receiver._task
            assert task is not None and not task.done()
            await receiver.close()
            assert task.done()
            await sender.publish(chan, '{"n":2}')
            await asyncio.sleep(0.2)
            assert received == ['{"n":1}']
        finally:
            await receiver.close()
            await sender.close()

    async def test_explicit_start_then_subscribe_receives(self):
        # The kernel/WS path may start the listener explicitly before any
        # subscription lands — start() is idempotent and subscription
        # commands ride the listener loop either way.
        prefix = f"fastplace:{_ns()}:"
        chan = "orders.42"
        receiver = make_bus(prefix)
        sender = make_bus(prefix)
        received: list[str] = []
        try:
            await receiver.start()
            receiver.subscribe(chan, lambda c, d: received.append(d))
            assert await wait_for(lambda: broker_knows(prefix, chan))
            await sender.publish(chan, '{"n":1}')
            assert await wait_for(lambda: bool(received))
        finally:
            await receiver.close()
            await sender.close()

    async def test_start_is_idempotent(self):
        bus = make_bus(f"fastplace:{_ns()}:")
        try:
            await bus.start()
            first = bus._task
            await bus.start()
            assert bus._task is first
        finally:
            await bus.close()


class TestRedisCloseIsRestartable:
    async def test_a_closed_bus_can_start_again(self):
        # Graceful shutdown closes the bus; a reborn process (or a test
        # sequence) must be able to re-establish the listener. Local
        # subscriptions survive close() — the same callback keeps receiving
        # once the fresh connection re-subscribes.
        prefix = f"fastplace:{_ns()}:"
        chan = "orders.42"
        bus = make_bus(prefix)
        sender = make_bus(prefix)
        received: list[str] = []
        try:
            bus.subscribe(chan, lambda c, d: received.append(d))
            assert await wait_for(lambda: broker_knows(prefix, chan))
            await sender.publish(chan, '{"n":1}')
            assert await wait_for(lambda: received == ['{"n":1}'])
            await bus.close()
            # Wait for the broker to drop the dead subscription before
            # restarting — otherwise the "known" poll can pass on the stale
            # entry and the fresh SUBSCRIBE lands after the publish.
            assert await wait_for(lambda: broker_forgets(prefix, chan))
            await bus.start()  # re-subscribes from surviving local state
            assert await wait_for(lambda: broker_knows(prefix, chan))
            await sender.publish(chan, '{"n":2}')
            assert await wait_for(lambda: received == ['{"n":1}', '{"n":2}'])
        finally:
            await bus.close()
            await sender.close()


async def _broker_channels() -> list[bytes]:
    """The broker's active pub/sub channel list, over a fresh connection."""
    import redis.asyncio as aioredis

    raw = aioredis.Redis.from_url(REDIS_URL)
    try:
        return list(await raw.pubsub_channels())
    finally:
        await raw.aclose()


async def broker_knows(prefix: str, chan: str) -> bool:
    """True once the broker lists the prefixed channel as actively
    subscribed — the deterministic gate for "subscribe landed before
    publish" (pub/sub does not replay)."""
    return f"{prefix}{chan}".encode() in await _broker_channels()


async def broker_forgets(prefix: str, chan: str) -> bool:
    """True once the broker no longer lists the channel — gate for
    unsubscribe/close having reached the broker (a "known" poll right
    after can otherwise pass on the stale entry)."""
    return f"{prefix}{chan}".encode() not in await _broker_channels()
