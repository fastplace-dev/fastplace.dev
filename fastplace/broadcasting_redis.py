"""Redis broadcast driver — cross-process pub/sub fan-out.

Same protocol as :class:`~fastplace.broadcasting.MemoryBroadcastBus`:
``subscribe(channel, callback) -> unsubscribe``, ``publish(channel, data)``
(strings only — payloads were serialized once at the ``broadcast()``
boundary), and ``close()``. A single background listener task owns the
broker pub/sub connection and dispatches inbound messages to local
subscribers, so one redis connection serves every socket in this process.

Every broker channel name hides behind ``BROADCAST_CHANNEL_PREFIX``
(default ``fastplace:broadcast:``), applied identically at publish and
subscribe — redis pub/sub is instance-global on the broker, and the prefix
keeps fastplace traffic out of every other app sharing the instance (the
``CACHE_PREFIX`` pattern). Callbacks receive the *logical* channel name
(prefix stripped) and the exact published string.

Delivery is at-most-once and unordered across processes — pub/sub, not a
log. The listener survives subscriber exceptions (logged, then the next
message dispatches); a dead listener is rebuilt by the next
``publish``/``start``/``subscribe`` from this process. Started lazily on
first use, or explicitly via :meth:`RedisBroadcastBus.start`; the kernel
registers a shutdown close under ``BROADCAST_DRIVER=redis``
(``fastplace.http.kernel``).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

from fastplace.broadcasting import Subscriber
from fastplace.config import config

__all__ = ["RedisBroadcastBus"]

logger = logging.getLogger("fastplace.broadcasting")

_DEFAULT_PREFIX = "fastplace:broadcast:"
_DEFAULT_REDIS_URL = "redis://localhost:6379/0"


class RedisBroadcastBus:
    """Pub/sub bus over one redis connection — multi-worker fan-out.

    ``url`` defaults to ``BROADCAST_REDIS_URL`` and falls back to
    ``QUEUE_REDIS_URL`` (one broker for both subsystems unless split
    deliberately); ``prefix`` defaults to ``BROADCAST_CHANNEL_PREFIX``.
    """

    def __init__(self, *, url: str | None = None, prefix: str | None = None) -> None:
        self._url = url or str(
            config("BROADCAST_REDIS_URL", default=None)
            or config("QUEUE_REDIS_URL", default=_DEFAULT_REDIS_URL)
        )
        self._prefix = (
            prefix
            if prefix is not None
            else str(config("BROADCAST_CHANNEL_PREFIX", default=_DEFAULT_PREFIX))
        )
        # Logical channel -> local callbacks. The broker side mirrors the
        # keys: a channel with zero callbacks must leave the pubsub set.
        self._subscribers: dict[str, list[Subscriber]] = {}
        self._pending_sub: set[str] = set()
        self._pending_unsub: set[str] = set()
        self._client: Any = None
        self._pubsub: Any = None
        self._task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()

    def _key(self, channel: str) -> str:
        """The broker-side channel name — prefix + logical channel."""
        return f"{self._prefix}{channel}"

    # -- protocol: subscribe / publish / close ---------------------------

    def subscribe(self, channel: str, callback: Subscriber) -> Callable[[], None]:
        """Register a delivery callback; returns its unsubscribe handle.

        The broker-side SUBSCRIBE rides the listener loop (subscribing from
        here would need the connection the listener owns). When a running
        event loop exists the listener is started immediately; from a sync
        context the command waits for the first ``publish``/``start``.
        """
        listeners = self._subscribers.setdefault(channel, [])
        listeners.append(callback)
        key = self._key(channel)
        self._pending_sub.add(key)
        self._pending_unsub.discard(key)
        self._schedule_start()
        return self._make_unsubscribe(channel, callback, key)

    async def publish(self, channel: str, data: str) -> None:
        """Publish the already-serialized ``data`` on ``channel``."""
        await self.start()
        client = self._client
        assert client is not None  # start() guarantees it under the lock
        await client.publish(self._key(channel), data)

    async def start(self) -> None:
        """Open the broker connection and listener (idempotent)."""
        async with self._start_lock:
            if self._task is not None and not self._task.done():
                return
            from redis.asyncio import Redis

            client = Redis.from_url(self._url)
            self._client = client
            self._pubsub = client.pubsub()
            self._task = asyncio.create_task(self._listen(), name="fastplace-broadcast-listener")

    async def close(self) -> None:
        """Stop the listener and release the broker connection.

        Local subscriptions survive: a later ``start()`` re-establishes the
        broker side from what is still registered (see ``_pending_sub``).
        """
        async with self._start_lock:
            task, self._task = self._task, None
            pubsub, self._pubsub = self._pubsub, None
            client, self._client = self._client, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for resource in (pubsub, client):
            if resource is None:
                continue
            try:
                await resource.aclose()
            except Exception:  # broker already gone — closing is best-effort
                logger.debug("broadcast bus close ignored broker error", exc_info=True)
        # The next start() opens a fresh pubsub with zero subscriptions;
        # re-queue every channel that still has local callbacks.
        self._pending_unsub.clear()
        self._pending_sub = {self._key(c) for c in self._subscribers}

    # -- internals ---------------------------------------------------------

    def _make_unsubscribe(self, channel: str, callback: Subscriber, key: str) -> Callable[[], None]:
        def _unsubscribe() -> None:
            # Remove one occurrence, not every equal callable — the same
            # function subscribed twice runs twice and unsubscribes once
            # (memory-driver parity).
            listeners = self._subscribers.get(channel)
            if listeners is None:
                return
            listeners.remove(callback)
            if listeners:
                return
            del self._subscribers[channel]
            self._pending_unsub.add(key)
            self._pending_sub.discard(key)

        return _unsubscribe

    def _schedule_start(self) -> None:
        """Start the listener when an event loop can carry it."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # sync context — first publish()/start() picks it up
        loop.create_task(self.start())

    async def _listen(self) -> None:
        pubsub = self._pubsub
        assert pubsub is not None
        try:
            while True:
                await self._sync_subscriptions()
                if not pubsub.subscribed:
                    # A publish-only bus (or one between unsubscribes) has
                    # no broker connection — get_message would raise, so
                    # idle-poll until a subscription lands.
                    await asyncio.sleep(0.05)
                    continue
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.25)
                if message is not None and message.get("type") == "message":
                    await self._dispatch(message)
        except asyncio.CancelledError:
            raise  # close() — not an error
        except Exception:
            # Broker loss or a redis client fault: this process stops
            # receiving until something calls publish/start/subscribe
            # again (delivery is at-most-once by design — documented).
            logger.exception("broadcast listener stopped — redis bus no longer receiving")

    async def _sync_subscriptions(self) -> None:
        """Drain pending broker-side SUBSCRIBE/UNSUBSCRIBE commands."""
        pubsub = self._pubsub
        assert pubsub is not None
        if self._pending_sub:
            keys = list(self._pending_sub)
            await pubsub.subscribe(*keys)
            for key in keys:
                self._pending_sub.discard(key)
        if self._pending_unsub:
            keys = list(self._pending_unsub)
            await pubsub.unsubscribe(*keys)
            for key in keys:
                self._pending_unsub.discard(key)

    async def _dispatch(self, message: dict[Any, Any]) -> None:
        raw = message.get("channel", b"")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not raw.startswith(self._prefix):
            return  # foreign traffic on the same broker — not ours
        channel = raw[len(self._prefix) :]
        data = message.get("data", "")
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        for callback in list(self._subscribers.get(channel, [])):
            try:
                result = callback(channel, data)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                # One bad subscriber never kills the listener for everyone
                # else — log and keep delivering.
                logger.exception("broadcast subscriber on channel %r raised; continuing", channel)
