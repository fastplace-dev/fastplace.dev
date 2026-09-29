"""Broadcasting — channel pub/sub behind one ``broadcast()`` API.

Blueprint §Broadcasting. Channels are dot-namespaced strings whose leading
segments decide the authorization rule applied at **subscribe time**:

- ``orders.42`` — public; anyone connected may subscribe.
- ``private.orders.42`` — requires an authenticated user plus a gate check
  (``BROADCAST_PRIVATE_ABILITY``; the *parsed* :class:`Channel` is passed to
  the ability so policies can decide per channel — row-level scoping is a
  launch target). Unset ability denies everyone: fail-closed, because
  "private but the ability name is missing" must never degrade to public.
- ``presence.orders.42`` — private rules plus a derived roster
  (:class:`PresenceTracker`).
- ``company.{id}.…`` — tenancy-scoped; core stays tenant-agnostic, so
  membership is decided by a :func:`register_channel_authorizer` hook that
  the ``fastplace-tenancy`` package installs (core alone never knows what a
  company is).

``broadcast(channel, payload)`` JSON-serializes **once** at this boundary —
fail-loud on non-JSON values (the error names the offending field path) and
capped by ``BROADCAST_MAX_PAYLOAD_BYTES``. Drivers move the serialized
string; they never re-serialize. Subscribe-time authorization is the
security boundary: there is no per-message re-check by design (see the
WebSocket endpoint for the documented revocation bound and
``broadcast.evict``).

This module is the bus + memory driver + presence roster; the redis driver
lives in ``fastplace.broadcasting_redis`` (same protocol, pub/sub fan-out)
and the socket layer in ``fastplace.http.broadcast_ws``.
"""

from __future__ import annotations

import inspect
import json
import logging
import math
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from fastplace.config import config
from fastplace.errors import ConfigurationError, FastplaceError

__all__ = [
    "BroadcastBus",
    "BroadcastError",
    "Channel",
    "Member",
    "MemoryBroadcastBus",
    "PresenceTracker",
    "authorize_subscribe",
    "broadcast",
    "broadcast_bus",
    "broadcast_events",
    "mapped_broadcast_channel",
    "parse_channel",
    "register_channel_authorizer",
    "reset_broadcasting",
    "set_broadcast_bus",
]

logger = logging.getLogger("fastplace.broadcasting")

#: Event name -> channel template, populated by :func:`broadcast_events`.
#: Empty by default: without an explicit registration no domain event ever
#: reaches a channel (the bridge is opt-in per event, reviewable by design).
_event_mappings: dict[str, str] = {}

#: Segments that only mean something in prefix position. Occurring anywhere
#: else makes the whole name unparseable — an ``orders.private.42`` shape
#: must be refused, never silently treated under the public rule.
_RESERVED = frozenset({"company", "private", "presence"})

#: Default per-payload cap (bytes of the serialized JSON). A slow consumer
#: is dropped, never allowed to balloon a channel's memory.
_DEFAULT_MAX_PAYLOAD_BYTES = 65536


class BroadcastError(FastplaceError):
    """A broadcast-channel or payload contract violation.

    Malformed channel names, unserializable payloads, oversized payloads —
    all fail loud at the API boundary rather than half-fanning-out.
    """

    status_code = 500
    default_message = "Broadcast error."


@dataclass(frozen=True)
class Channel:
    """One parsed channel name.

    ``kind`` is ``"public" | "private" | "presence"``; ``name`` is the
    channel after the prefix segments; ``tenant_id`` is the parsed company
    id when a ``company.{id}.`` prefix is present. Abilities and authorizer
    hooks receive this object — the raw string never reaches policy code.
    """

    raw: str
    kind: str
    name: str
    tenant_id: int | None = None


def parse_channel(raw: str) -> Channel:
    """Parse a channel name under the normative grammar.

    ``channel := company "." tenant_id "." rest | rest`` and
    ``rest := "private." name | "presence." name | name``. Malformed names
    raise :class:`BroadcastError` — an unknown shape never falls through to
    a weaker rule.
    """
    if not isinstance(raw, str):
        raise TypeError(f"channel name must be a str, got {type(raw).__name__}")
    segments = raw.split(".")
    if not raw or any(segment == "" for segment in segments):
        raise BroadcastError(f"malformed channel name {raw!r} — empty segment")

    tenant_id: int | None = None
    index = 0
    if segments[0] == "company":
        # company prefix is outermost and carries a positive integer id.
        if len(segments) < 3 or not segments[1].isdigit() or int(segments[1]) <= 0:
            raise BroadcastError(
                f"malformed channel name {raw!r} — expected company.{{id}} with a "
                "positive integer id, e.g. company.7.orders.42"
            )
        tenant_id = int(segments[1])
        index = 2

    kind = "public"
    kind_position: int | None = None
    if index < len(segments) and segments[index] in ("private", "presence"):
        kind = segments[index]
        kind_position = index
        index += 1
    if index >= len(segments):
        raise BroadcastError(f"malformed channel name {raw!r} — no channel after prefixes")

    name = ".".join(segments[index:])
    for position, segment in enumerate(segments):
        prefix_position = (position == 0 and segment == "company") or (
            kind_position is not None and position == kind_position
        )
        if segment in _RESERVED and not prefix_position:
            raise BroadcastError(
                f"malformed channel name {raw!r} — {segment!r} is only valid as a leading prefix"
            )

    return Channel(raw=raw, kind=kind, name=name, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# subscribe-time authorization
# ---------------------------------------------------------------------------

#: ``True`` admits, ``False`` denies, ``None`` abstains. The first non-None
#: verdict decides; a channel nobody weighs in on falls to the default rule
#: (ability-only for private/presence, open for public). Sync or async.
ChannelAuthorizer = Callable[[Any, Channel], Any]

_authorizers: list[ChannelAuthorizer] = []


def register_channel_authorizer(fn: ChannelAuthorizer) -> None:
    """Register a channel authorizer (the tenancy seam).

    Authorizers run in registration order at subscribe time. This is how
    ``fastplace-tenancy`` vouches for ``company.{id}`` membership without
    core ever importing tenancy.
    """
    _authorizers.append(fn)


async def authorize_subscribe(user: Any, channel: Channel) -> bool:
    """Decide whether ``user`` may subscribe to ``channel``.

    The authorizer chain answers first (True admits, False denies, None
    abstains). Otherwise: public channels admit anyone; private and
    presence channels require an authenticated user **and**
    ``gate.allows(user, BROADCAST_PRIVATE_ABILITY, channel)`` — the parsed
    channel rides as the gate argument so policies decide per channel. An
    unset ability denies (fail-closed); an ability nobody defined surfaces
    the gate's ``ConfigurationError`` (fail loud, never silently allow).
    """
    for fn in _authorizers:
        verdict = fn(user, channel)
        if inspect.isawaitable(verdict):
            verdict = await verdict
        if verdict is not None:
            return bool(verdict)

    if channel.kind == "public":
        return True
    if user is None:
        return False
    ability = config("BROADCAST_PRIVATE_ABILITY", default=None)
    if not ability:
        return False
    from fastplace.authz import gate

    return await gate.allows(user, str(ability), channel)


# ---------------------------------------------------------------------------
# serialization — once, at the boundary, fail-loud
# ---------------------------------------------------------------------------


def _offender(value: Any, path: str) -> str | None:
    """Path of the first value json cannot represent, else ``None``.

    JSON-native scalars pass; containers recurse; everything else (bytes,
    datetime, UUID, Decimal, sets, objects) reports its dotted path so the
    error names the field instead of making the caller guess.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return None
    if isinstance(value, float):
        return path if (math.isnan(value) or math.isinf(value)) else None
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            found = _offender(item, child)
            if found is not None:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            found = _offender(item, f"{path}[{i}]")
            if found is not None:
                return found
        return None
    return path or "$"


def serialize_payload(payload: Any) -> str:
    """JSON-serialize a broadcast payload once, fail-loud.

    A payload carrying values json cannot represent raises
    :class:`BroadcastError` naming the offending field path — silent
    ``default=str`` coercion would let unserializable data fan out
    half-processed. The serialized size is capped by
    ``BROADCAST_MAX_PAYLOAD_BYTES``.
    """
    offender = _offender(payload, "")
    if offender is not None:
        raise BroadcastError(
            f"broadcast payload field {offender!r} is not JSON-serializable — "
            "convert dates, UUIDs, Decimals, and bytes to str/int before broadcasting"
        )
    data = json.dumps(payload, allow_nan=False, separators=(",", ":"))
    cap = int(config("BROADCAST_MAX_PAYLOAD_BYTES", default=_DEFAULT_MAX_PAYLOAD_BYTES))
    if len(data.encode("utf-8")) > cap:
        raise BroadcastError(
            f"broadcast payload is {len(data)} bytes — over the "
            f"BROADCAST_MAX_PAYLOAD_BYTES cap of {cap}"
        )
    return data


def _job_id() -> str | None:
    """The current queue-job id, when running inside a worker."""
    from fastplace.logging.context import get_job_id

    return get_job_id() or None


# ---------------------------------------------------------------------------
# the bus — memory driver
# ---------------------------------------------------------------------------

#: Delivery callback: ``(channel name, serialized payload)``. Sync or async.
Subscriber = Callable[[str, str], Any]


class BroadcastBus(Protocol):
    """The driver contract: subscribe/publish/close over serialized strings."""

    def subscribe(self, channel: str, callback: Subscriber) -> Callable[[], None]: ...

    async def publish(self, channel: str, data: str) -> None: ...

    async def close(self) -> None: ...


class MemoryBroadcastBus:
    """In-process pub/sub — deterministic dev and tests, no broker.

    Per-channel delivery runs callbacks sequentially in subscription order,
    so per-channel ordering is preserved by construction. There is no
    cross-process traffic: a publish here reaches this process only.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscriber]] = {}

    def subscribe(self, channel: str, callback: Subscriber) -> Callable[[], None]:
        """Register a delivery callback; returns its unsubscribe handle."""
        listeners = self._subscribers.setdefault(channel, [])
        listeners.append(callback)

        def _unsubscribe() -> None:
            # Remove one occurrence, not every equal callable — the same
            # function subscribed twice runs twice and unsubscribes once.
            listeners.remove(callback)

        return _unsubscribe

    async def publish(self, channel: str, data: str) -> None:
        """Deliver ``data`` to every subscriber of ``channel``, in order."""
        for callback in list(self._subscribers.get(channel, [])):
            result = callback(channel, data)
            if inspect.isawaitable(result):
                await result

    async def close(self) -> None:
        """Release driver resources (the memory driver holds none)."""


_bus: BroadcastBus | None = None


def broadcast_bus() -> BroadcastBus:
    """The process broadcast bus, built once behind ``BROADCAST_DRIVER``.

    Only the in-process memory driver lives here; the redis pub/sub driver
    is ``fastplace.broadcasting_redis.RedisBroadcastBus`` (same
    :class:`BroadcastBus` protocol, broker fan-out).
    """
    global _bus
    if _bus is None:
        driver = str(config("BROADCAST_DRIVER", default="memory")).lower()
        if driver != "memory":
            try:
                from fastplace.broadcasting_redis import RedisBroadcastBus

                _bus = RedisBroadcastBus()
            except ImportError as exc:  # pragma: no cover — redis extra missing
                raise ConfigurationError(
                    f"unknown BROADCAST_DRIVER {driver!r} or its dependency is "
                    f"missing ({exc}) — expected memory or redis"
                ) from exc
        else:
            _bus = MemoryBroadcastBus()
    return _bus


def set_broadcast_bus(bus: BroadcastBus) -> None:
    """Swap the process bus (DI seam — tests and the redis subscriber)."""
    global _bus
    _bus = bus


def reset_broadcasting() -> None:
    """Clear the bus singleton, the authorizer chain, and the event map."""
    global _bus
    _bus = None
    _authorizers.clear()
    _event_mappings.clear()


async def broadcast(channel: str, payload: Any) -> None:
    """Serialize ``payload`` once and publish it on ``channel``.

    Authorization is subscribe-time only — publishing needs no user. Under
    ``BROADCAST_DRIVER=memory`` a call from a queue worker reaches no
    browser (the bus is in-process), so it warns instead of pretending to
    fan out.
    """
    parsed = parse_channel(channel)
    data = serialize_payload(payload)
    if isinstance(broadcast_bus(), MemoryBroadcastBus):
        job = _job_id()
        if job:
            logger.warning(
                "broadcast() on channel %r under BROADCAST_DRIVER=memory from "
                "worker context (job %s) — no browser will receive this event; "
                "set BROADCAST_DRIVER=redis for cross-process fan-out",
                channel,
                job,
            )
    await broadcast_bus().publish(parsed.raw, data)


# ---------------------------------------------------------------------------
# the event bridge — explicit domain-event → channel mapping
# ---------------------------------------------------------------------------


def broadcast_events(mapping: Mapping[str, str]) -> None:
    """Map domain event names to channel templates, opt-in per event.

    ``broadcast_events({"order_created": "orders.{order_id}"})`` makes that
    one event publish on ``orders.<id>`` (placeholders interpolate from the
    event payload); a template without placeholders is a constant channel.
    Events without a mapping are never broadcast — a blanket ``*`` listener
    would push every model lifecycle event onto channels, an unauditable
    security surface. Call from app wiring; later calls compose per key.
    """
    for name, template in mapping.items():
        if not isinstance(name, str) or not name:
            raise TypeError(f"event name must be a non-empty str, got {name!r}")
        if not isinstance(template, str) or not template:
            raise TypeError(f"channel template must be a non-empty str, got {template!r}")
        _event_mappings[name] = template


def mapped_broadcast_channel(name: str, payload: Mapping[str, Any]) -> str | None:
    """Render the mapped channel for ``name``, or ``None`` when unmapped.

    A mapped name whose template references a key the payload lacks is a
    wiring bug, not a runtime condition — it fails loud, naming the event.
    """
    template = _event_mappings.get(name)
    if template is None:
        return None
    try:
        return template.format_map(dict(payload))
    except KeyError as exc:
        raise BroadcastError(
            f"broadcast_events mapping for {name!r} references {exc.args[0]!r} "
            f"but the event payload carries no such key"
        ) from None


# ---------------------------------------------------------------------------
# presence — the derived roster
# ---------------------------------------------------------------------------

#: Control traffic rides a shadow channel per presence channel. The double
#: colon keeps it out of the user grammar — parse_channel refuses such
#: names, so a client can never subscribe to roster plumbing.
_CONTROL_PREFIX = "__presence::"


@dataclass(frozen=True)
class Member:
    """One roster entry — a user id, optional display metadata, and how many
    connections (tabs) that user holds. Presence metadata is user-chosen
    display data, never server-side PII."""

    user_id: int | str
    metadata: Mapping[str, Any] | None
    connections: int


@dataclass
class _LocalMember:
    metadata: Mapping[str, Any] | None = None
    connections: int = 0


class PresenceTracker:
    """Derive presence rosters from local connections plus bus snapshots.

    No separate roster store: each process tracks its own connections and
    republishes a **full local snapshot** as its heartbeat (keyed by the
    originating process id); receivers reconcile by diff. A crashed
    process's snapshot stops arriving and its members age out after
    ``ghost_ttl`` — bounded ghost lifetime, documented as eventual. The
    roster is keyed by user id with a connection count, so two tabs of one
    user are one member. Join/leave control events give remote processes
    immediate updates between heartbeats.
    """

    def __init__(
        self,
        *,
        bus: BroadcastBus | None = None,
        clock: Callable[[], float] = time.monotonic,
        origin: str | None = None,
        ghost_ttl: float = 45.0,
        on_change: Callable[[str, list[Member]], None] | None = None,
    ) -> None:
        self._bus = bus if bus is not None else broadcast_bus()
        self._clock = clock
        self._origin = origin or uuid.uuid4().hex
        self._ghost_ttl = ghost_ttl
        self._on_change = on_change
        self._local: dict[str, dict[int | str, _LocalMember]] = {}
        # channel -> origin -> (members snapshot, last-seen clock)
        self._remote: dict[str, dict[str, tuple[dict[int | str, dict], float]]] = {}
        self._unsubscribes: dict[str, Callable[[], None]] = {}
        self._last_roster: dict[str, list] = {}

    # -- local membership ------------------------------------------------

    async def join(self, channel: str, user_id: int | str, metadata: Any = None) -> None:
        """Record a local connection (one tab) on a presence channel."""
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TypeError("presence metadata must be a mapping or None")
        self._listen(channel)
        member = self._local.setdefault(channel, {}).get(user_id) or _LocalMember()
        if metadata is not None:
            member.metadata = metadata
        member.connections += 1
        self._local[channel][user_id] = member
        await self._publish(
            channel, {"kind": "join", "user_id": user_id, "metadata": member.metadata}
        )
        self._changed(channel)

    async def leave(self, channel: str, user_id: int | str) -> None:
        """Drop one local connection; a member leaves when the last tab closes."""
        member = self._local.get(channel, {}).get(user_id)
        if member is None:
            return  # leaving without joining is a no-op
        member.connections -= 1
        if member.connections > 0:
            self._local[channel][user_id] = member
            return
        del self._local[channel][user_id]
        await self._publish(channel, {"kind": "leave", "user_id": user_id})
        self._changed(channel)

    async def heartbeat(self) -> None:
        """Republish the full local member list per channel, keyed by origin.

        Receivers reconcile by diff and keep the last-seen stamp, so a
        process that dies simply stops heartbeating and its members age out.
        """
        for channel, members in self._local.items():
            if not members:
                continue
            # A list, never a dict keyed by user id: JSON object keys are
            # always strings, which would corrupt integer user ids on the
            # receiving side. Values keep their types.
            snapshot = [
                {"user_id": user_id, "metadata": m.metadata, "connections": m.connections}
                for user_id, m in members.items()
            ]
            await self._publish(channel, {"kind": "snapshot", "members": snapshot})

    def roster(self, channel: str) -> list[Member]:
        """The merged roster: local members plus fresh remote snapshots."""
        merged: dict[int | str, Member] = {}
        for user_id, member in self._local.get(channel, {}).items():
            merged[user_id] = Member(user_id, member.metadata, member.connections)
        now = self._clock()
        for origin in self._remote.get(channel, {}):
            snapshot, last_seen = self._remote[channel][origin]
            if now - last_seen > self._ghost_ttl:
                continue  # ghost — the origin stopped heartbeating
            for user_id, info in snapshot.items():
                existing = merged.get(user_id)
                connections = int(info.get("connections", 1))
                if existing is None:
                    merged[user_id] = Member(user_id, info.get("metadata"), connections)
                else:
                    # Same user on two processes (two machines, two tabs):
                    # one member, summed connections.
                    merged[user_id] = Member(
                        existing.user_id, existing.metadata, existing.connections + connections
                    )
        return [merged[user_id] for user_id in sorted(merged, key=str)]

    # -- plumbing ---------------------------------------------------------

    def watch(self, channel: str) -> None:
        """Observe a presence channel's roster without joining it.

        Membership (``join``) is the WebSocket layer's path and subscribes
        to control traffic on first join; ``watch`` is the inspection seam —
        a process (or test) that wants the roster without being a member.
        """
        self._listen(channel)

    def _listen(self, channel: str) -> None:
        """Subscribe to the channel's control traffic once."""
        if channel in self._unsubscribes:
            return

        def _handle(control_channel: str, data: str) -> None:
            self._receive(control_channel, data)

        self._unsubscribes[channel] = self._bus.subscribe(f"{_CONTROL_PREFIX}{channel}", _handle)

    async def _publish(self, channel: str, message: dict[str, Any]) -> None:
        await self._bus.publish(
            f"{_CONTROL_PREFIX}{channel}",
            json.dumps({**message, "origin": self._origin}, separators=(",", ":")),
        )

    def _receive(self, control_channel: str, data: str) -> None:
        """Fold one control message (join/leave/snapshot) into the view."""
        channel = control_channel.removeprefix(_CONTROL_PREFIX)
        message = json.loads(data)
        origin = message["origin"]
        if origin == self._origin:
            return  # own echo — the local dict is already authoritative
        snapshots = self._remote.setdefault(channel, {})
        if message["kind"] == "join":
            snapshot, last_seen = snapshots.get(origin, ({}, self._clock()))
            members = dict(snapshot)
            members[message["user_id"]] = {
                "metadata": message.get("metadata"),
                "connections": 1,
            }
            snapshots[origin] = (members, last_seen)
        elif message["kind"] == "leave":
            snapshot, last_seen = snapshots.get(origin, ({}, self._clock()))
            members = dict(snapshot)
            members.pop(message["user_id"], None)
            snapshots[origin] = (members, last_seen)
        else:  # snapshot — authoritative replacement for that origin
            members = {
                entry["user_id"]: {
                    "metadata": entry.get("metadata"),
                    "connections": int(entry.get("connections", 1)),
                }
                for entry in message["members"]
            }
            snapshots[origin] = (members, self._clock())
        self._changed(channel)

    def _changed(self, channel: str) -> None:
        """Fire ``on_change`` when the roster actually changed (diff-only)."""
        if self._on_change is None:
            return
        roster = self.roster(channel)
        signature = [(m.user_id, m.connections) for m in roster]
        if self._last_roster.get(channel) == signature:
            return
        self._last_roster[channel] = signature
        self._on_change(channel, roster)
