"""Broadcasting bus — taxonomy parsing, subscribe-time authorization, the
memory driver, and the derived presence roster.

Everything here runs in-process against the memory driver or explicit
``MemoryBroadcastBus`` instances: no broker, no network, no sockets. The
redis driver has its own suite (``test_redis_driver.py``); the WebSocket
endpoint has ``tests/http/test_broadcast_websocket.py``.
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest

from fastplace.broadcasting import (
    BroadcastError,
    Channel,
    MemoryBroadcastBus,
    PresenceTracker,
    authorize_subscribe,
    broadcast,
    broadcast_bus,
    parse_channel,
    register_channel_authorizer,
    reset_broadcasting,
    set_broadcast_bus,
)
from fastplace.errors import ConfigurationError


@pytest.fixture(autouse=True)
def _clean_broadcasting(monkeypatch):
    """Fresh bus, no authorizers, no broadcast env, per test."""
    reset_broadcasting()
    monkeypatch.delenv("BROADCAST_DRIVER", raising=False)
    monkeypatch.delenv("BROADCAST_PRIVATE_ABILITY", raising=False)
    monkeypatch.delenv("BROADCAST_MAX_PAYLOAD_BYTES", raising=False)
    yield
    reset_broadcasting()


class User:
    """Minimal stand-in — authorize_subscribe only reads attributes."""

    def __init__(self, id=1, company_id=None, is_ops=False):
        self.id = id
        self.company_id = company_id
        self.is_ops = is_ops


# ---------------------------------------------------------------------------
# channel taxonomy — the normative grammar
# ---------------------------------------------------------------------------


class TestParseChannel:
    def test_public_channel(self):
        channel = parse_channel("orders.42")
        assert channel == Channel(raw="orders.42", kind="public", name="orders.42", tenant_id=None)

    def test_private_channel(self):
        channel = parse_channel("private.orders.42")
        assert channel.kind == "private"
        assert channel.name == "orders.42"

    def test_presence_channel(self):
        channel = parse_channel("presence.orders.42")
        assert channel.kind == "presence"
        assert channel.name == "orders.42"

    def test_tenant_prefix_is_outermost(self):
        channel = parse_channel("company.7.private.orders.42")
        assert channel.tenant_id == 7
        assert channel.kind == "private"
        assert channel.name == "orders.42"
        assert channel.raw == "company.7.private.orders.42"

    def test_tenant_public_channel(self):
        channel = parse_channel("company.7.orders.42")
        assert channel.tenant_id == 7
        assert channel.kind == "public"

    def test_reserved_segment_in_non_prefix_position_is_refused(self):
        # An "orders.private.42" shape must never fall through to the public
        # rule — reserved words only mean something in prefix position, so a
        # non-prefix occurrence makes the whole name unparseable.
        for raw in (
            "orders.private.42",
            "foo.company.bar",
            "a.presence.b",
            "company.7.company.8.x",
            "private.presence.x",
        ):
            with pytest.raises(BroadcastError):
                parse_channel(raw)

    def test_malformed_names_are_refused(self):
        for raw in (
            "company.foo.orders",
            "company..orders",
            "company.0.x",
            "company.-3.x",
            "private.",
            "presence.",
            "",
            ".x",
            "x.",
            "orders..42",
        ):
            with pytest.raises(BroadcastError):
                parse_channel(raw)

    def test_control_channel_names_are_refused(self):
        # Presence plumbing rides shadow channels named "__presence::<raw>".
        # The grammar must refuse those names so a client cannot subscribe
        # to roster control traffic (join/leave/snapshot frames carrying
        # user ids) as if it were a public channel — "_" prefixes and "::"
        # separators never appear in the user grammar.
        for raw in (
            "__presence::presence.demo",
            "__presence::presence.orders.42",
            "__presence::company.7.presence.room",
            "__presence::anything",
            "_private.orders.42",
            "orders::42",
        ):
            with pytest.raises(BroadcastError):
                parse_channel(raw)

    def test_non_string_name_is_a_type_error(self):
        with pytest.raises(TypeError):
            parse_channel(42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# subscribe-time authorization
# ---------------------------------------------------------------------------


class TestAuthorizeSubscribe:
    async def test_public_channel_admits_anonymous(self):
        assert await authorize_subscribe(None, parse_channel("orders.42"))

    async def test_private_channel_requires_authentication(self):
        assert not await authorize_subscribe(None, parse_channel("private.orders.42"))

    async def test_presence_channel_requires_authentication(self):
        assert not await authorize_subscribe(None, parse_channel("presence.orders.42"))

    async def test_private_fails_closed_without_ability(self, monkeypatch):
        # "Private but the ability name is missing" must never degrade to
        # public — unset ability denies everyone. Pinned to "" so the sample
        # app's config default cannot flip the premise: this tests the
        # framework's no-ability branch, not the app's config choice.
        monkeypatch.setenv("BROADCAST_PRIVATE_ABILITY", "")
        assert not await authorize_subscribe(User(), parse_channel("private.orders.42"))

    async def test_presence_fails_closed_without_ability(self, monkeypatch):
        monkeypatch.setenv("BROADCAST_PRIVATE_ABILITY", "")
        assert not await authorize_subscribe(User(), parse_channel("presence.orders.42"))

    async def test_tenant_channels_fail_closed_without_an_authorizer(self):
        # The grammar parses company.{id} as a trust-relevant tenant-scoped
        # shape, so the core's default rule must not degrade it to public.
        # Without the tenancy authorizer registered (package absent or
        # install_tenant_broadcasting() not called) nobody vouches for
        # membership — deny, exactly like an unset private ability. A
        # miswired app loses subscription loudly at the handshake instead
        # of silently streaming cross-tenant.
        assert not await authorize_subscribe(User(), parse_channel("company.7.orders.42"))
        assert not await authorize_subscribe(None, parse_channel("company.7.orders.42"))
        assert not await authorize_subscribe(User(), parse_channel("company.7.private.x"))

    async def test_ability_receives_the_parsed_channel(self, monkeypatch):
        from fastplace.authz import gate

        seen: list[Channel] = []

        @gate.define("view-broadcast")
        async def view_broadcast(user, channel):
            seen.append(channel)
            return channel.name == "orders.42"

        monkeypatch.setenv("BROADCAST_PRIVATE_ABILITY", "view-broadcast")
        try:
            assert await authorize_subscribe(User(), parse_channel("private.orders.42"))
            assert not await authorize_subscribe(User(), parse_channel("private.invoices.1"))
            # The gate saw the Channel object, not the raw string — row-level
            # policies key off tenant id and name through it.
            assert all(isinstance(c, Channel) for c in seen)
            assert seen[0].name == "orders.42"
        finally:
            gate.reset()

    async def test_undefined_ability_fails_loud(self, monkeypatch):
        from fastplace.authz import gate

        monkeypatch.setenv("BROADCAST_PRIVATE_ABILITY", "no-such-ability")
        try:
            with pytest.raises(ConfigurationError, match="no-such-ability"):
                await authorize_subscribe(User(), parse_channel("private.orders.42"))
        finally:
            gate.reset()

    async def test_authorizer_chain_first_verdict_wins(self):
        # True admits, False denies, None abstains — the first non-None
        # verdict decides; nobody weighing in falls to the default rule.
        register_channel_authorizer(lambda user, channel: None)
        register_channel_authorizer(lambda user, channel: False)
        register_channel_authorizer(lambda user, channel: True)  # never reached
        assert not await authorize_subscribe(User(), parse_channel("private.orders.42"))

    async def test_authorizer_chain_can_admit_tenant_channels(self):
        # The tenancy seam: a membership authorizer vouches for company
        # channels without any ability being configured (core stays
        # tenant-agnostic; fastplace-tenancy installs the real one).
        def membership(user, channel):
            if channel.tenant_id is None:
                return None
            return getattr(user, "company_id", None) == channel.tenant_id

        register_channel_authorizer(membership)
        assert await authorize_subscribe(User(company_id=7), parse_channel("company.7.orders.42"))
        assert not await authorize_subscribe(
            User(company_id=8), parse_channel("company.7.private.orders.42")
        )

    async def test_authorizer_chain_sync_and_async_both_answer(self):
        async def deny_all(user, channel):
            return False

        register_channel_authorizer(deny_all)
        assert not await authorize_subscribe(User(), parse_channel("private.orders.42"))


# ---------------------------------------------------------------------------
# the memory driver — delivery, isolation, ordering
# ---------------------------------------------------------------------------


class TestMemoryBus:
    async def test_subscriber_receives_serialized_payload(self):
        bus = broadcast_bus()
        received: list[str] = []
        bus.subscribe("orders.42", lambda channel, data: received.append(data))
        await broadcast("orders.42", {"hello": "world"})
        assert len(received) == 1
        assert json.loads(received[0]) == {"hello": "world"}

    async def test_no_cross_channel_delivery(self):
        bus = broadcast_bus()
        orders: list[str] = []
        invoices: list[str] = []
        bus.subscribe("orders.42", lambda c, d: orders.append(d))
        bus.subscribe("invoices.1", lambda c, d: invoices.append(d))
        await broadcast("orders.42", {"n": 1})
        assert orders and not invoices

    async def test_unsubscribe_stops_delivery(self):
        bus = broadcast_bus()
        received: list[str] = []
        unsub = bus.subscribe("orders.42", lambda c, d: received.append(d))
        unsub()
        await broadcast("orders.42", {"n": 1})
        assert received == []

    async def test_per_channel_order_preserved(self):
        bus = broadcast_bus()
        received: list[int] = []
        bus.subscribe("orders.42", lambda c, d: received.append(json.loads(d)["n"]))
        for n in range(5):
            await broadcast("orders.42", {"n": n})
        assert received == [0, 1, 2, 3, 4]

    async def test_broadcast_rejects_malformed_channel(self):
        with pytest.raises(BroadcastError):
            await broadcast("orders.private.42", {"n": 1})

    async def test_factory_returns_memory_singleton(self):
        assert isinstance(broadcast_bus(), MemoryBroadcastBus)
        assert broadcast_bus() is broadcast_bus()

    async def test_set_broadcast_bus_swaps_the_driver(self):
        replacement = MemoryBroadcastBus()
        set_broadcast_bus(replacement)
        assert broadcast_bus() is replacement
        reset_broadcasting()
        assert broadcast_bus() is not replacement

    async def test_async_subscribers_are_awaited(self):
        bus = broadcast_bus()
        received: list[str] = []

        async def async_cb(channel, data):
            received.append(data)

        bus.subscribe("orders.42", async_cb)
        await broadcast("orders.42", {"n": 1})
        assert received


# ---------------------------------------------------------------------------
# serialization — once, at the boundary, fail-loud
# ---------------------------------------------------------------------------


class TestSerialization:
    async def test_unserializable_field_is_named(self):
        import datetime

        payload = {"ok": 1, "when": datetime.datetime(2026, 1, 1)}
        with pytest.raises(BroadcastError, match="when"):
            await broadcast("orders.42", payload)

    async def test_uuid_and_decimal_are_named(self):
        payload = {"id": uuid.uuid4()}
        with pytest.raises(BroadcastError, match="id"):
            await broadcast("orders.42", payload)

    async def test_bytes_are_rejected(self):
        with pytest.raises(BroadcastError, match="blob"):
            await broadcast("orders.42", {"blob": b"raw"})

    async def test_nested_offender_is_named_by_path(self):
        import datetime

        payload = {"order": {"meta": {"at": datetime.datetime(2026, 1, 1)}}}
        with pytest.raises(BroadcastError, match=r"order\.meta\.at"):
            await broadcast("orders.42", payload)

    async def test_payload_size_is_capped(self, monkeypatch):
        monkeypatch.setenv("BROADCAST_MAX_PAYLOAD_BYTES", "64")
        with pytest.raises(BroadcastError, match="BROADCAST_MAX_PAYLOAD_BYTES"):
            await broadcast("orders.42", {"data": "x" * 200})

    async def test_memory_driver_warns_from_worker_context(self, monkeypatch, caplog):
        # BROADCAST_DRIVER=memory from a queue worker reaches no browser — a
        # warning says so instead of pretending to fan out.
        import fastplace.broadcasting as broadcasting

        monkeypatch.setattr(broadcasting, "_job_id", lambda: "job-9")
        with caplog.at_level(logging.WARNING, logger="fastplace.broadcasting"):
            await broadcast("orders.42", {"n": 1})
        assert any("memory" in r.message and "worker" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# presence — the derived roster
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_tracker(bus=None, clock=None, origin=None, **kwargs) -> PresenceTracker:
    return PresenceTracker(
        bus=bus or broadcast_bus(),
        clock=clock or FakeClock(),
        origin=origin or uuid.uuid4().hex,
        **kwargs,
    )


class TestPresenceRoster:
    async def test_join_adds_member_with_metadata(self):
        tracker = make_tracker()
        await tracker.join("presence.orders.42", 7, {"name": "Ada"})
        roster = tracker.roster("presence.orders.42")
        assert [(m.user_id, m.metadata, m.connections) for m in roster] == [(7, {"name": "Ada"}, 1)]

    async def test_two_tabs_are_one_member(self):
        tracker = make_tracker()
        await tracker.join("presence.orders.42", 7)
        await tracker.join("presence.orders.42", 7)
        roster = tracker.roster("presence.orders.42")
        assert len(roster) == 1
        assert roster[0].connections == 2
        await tracker.leave("presence.orders.42", 7)
        assert tracker.roster("presence.orders.42")[0].connections == 1
        await tracker.leave("presence.orders.42", 7)
        assert tracker.roster("presence.orders.42") == []

    async def test_distinct_users_are_distinct_members(self):
        tracker = make_tracker()
        await tracker.join("presence.orders.42", 7)
        await tracker.join("presence.orders.42", 9)
        assert sorted(m.user_id for m in tracker.roster("presence.orders.42")) == [7, 9]

    async def test_rosters_are_per_channel(self):
        tracker = make_tracker()
        await tracker.join("presence.orders.42", 7)
        await tracker.join("presence.invoices.1", 9)
        assert [m.user_id for m in tracker.roster("presence.orders.42")] == [7]
        assert [m.user_id for m in tracker.roster("presence.invoices.1")] == [9]

    async def test_metadata_must_be_a_mapping_or_none(self):
        tracker = make_tracker()
        with pytest.raises(TypeError):
            await tracker.join("presence.orders.42", 7, "Ada")  # type: ignore[arg-type]

    async def test_leave_without_join_is_a_no_op(self):
        tracker = make_tracker()
        await tracker.leave("presence.orders.42", 7)
        assert tracker.roster("presence.orders.42") == []


class TestPresenceAcrossProcesses:
    async def test_snapshot_heartbeat_feeds_remote_rosters(self):
        clock = FakeClock()
        bus = MemoryBroadcastBus()
        here = make_tracker(bus=bus, clock=clock, origin="here")
        there = make_tracker(bus=bus, clock=clock, origin="there")
        there.watch("presence.orders.42")

        await here.join("presence.orders.42", 7, {"name": "Ada"})
        await here.heartbeat()
        roster = there.roster("presence.orders.42")
        assert [m.user_id for m in roster] == [7]

    async def test_ghost_members_age_out_past_the_ttl(self):
        clock = FakeClock()
        bus = MemoryBroadcastBus()
        here = make_tracker(bus=bus, clock=clock, origin="here")
        there = make_tracker(bus=bus, clock=clock, origin="there", ghost_ttl=30.0)
        there.watch("presence.orders.42")

        await here.join("presence.orders.42", 7)
        await here.heartbeat()
        assert there.roster("presence.orders.42")
        clock.advance(31.0)
        assert there.roster("presence.orders.42") == []
        # A fresh snapshot resurrects the member (the process was alive, just
        # briefly late — aging out is reconciliation, not a ban).
        await here.heartbeat()
        assert [m.user_id for m in there.roster("presence.orders.42")] == [7]

    async def test_remote_leave_is_immediate(self):
        clock = FakeClock()
        bus = MemoryBroadcastBus()
        here = make_tracker(bus=bus, clock=clock, origin="here")
        there = make_tracker(bus=bus, clock=clock, origin="there")
        there.watch("presence.orders.42")

        await here.join("presence.orders.42", 7)
        await here.heartbeat()
        assert there.roster("presence.orders.42")
        await here.leave("presence.orders.42", 7)
        assert there.roster("presence.orders.42") == []

    async def test_remote_and_local_members_merge_by_user(self):
        clock = FakeClock()
        bus = MemoryBroadcastBus()
        here = make_tracker(bus=bus, clock=clock, origin="here")
        there = make_tracker(bus=bus, clock=clock, origin="there")
        there.watch("presence.orders.42")

        await here.join("presence.orders.42", 7)
        await here.heartbeat()
        await there.join("presence.orders.42", 7)  # same user, this process
        await there.join("presence.orders.42", 9)
        roster = there.roster("presence.orders.42")
        by_user = {m.user_id: m.connections for m in roster}
        assert by_user == {7: 2, 9: 1}

    async def test_snapshot_carries_connection_counts(self):
        clock = FakeClock()
        bus = MemoryBroadcastBus()
        here = make_tracker(bus=bus, clock=clock, origin="here")
        there = make_tracker(bus=bus, clock=clock, origin="there")
        there.watch("presence.orders.42")

        await here.join("presence.orders.42", 7)
        await here.join("presence.orders.42", 7)  # two tabs on "here"
        await here.heartbeat()
        roster = there.roster("presence.orders.42")
        assert [(m.user_id, m.connections) for m in roster] == [(7, 2)]

    async def test_on_change_fires_on_local_and_remote_transitions(self):
        clock = FakeClock()
        bus = MemoryBroadcastBus()
        here = make_tracker(bus=bus, clock=clock, origin="here")
        changes: list[tuple[str, list[int]]] = []

        def on_change(channel: str, roster) -> None:
            changes.append((channel, sorted(m.user_id for m in roster)))

        there = make_tracker(bus=bus, clock=clock, origin="there", on_change=on_change)
        there.watch("presence.orders.42")

        await here.join("presence.orders.42", 7)
        await here.heartbeat()
        assert changes[-1] == ("presence.orders.42", [7])
        await here.leave("presence.orders.42", 7)
        assert changes[-1] == ("presence.orders.42", [])

    async def test_remote_multi_tab_counts_survive_between_heartbeats(self):
        # Control events carry the post-change connection count, or a remote
        # process collapses two tabs into one (join says 1) and drops the
        # member on the first tab close (leave says gone) until the next
        # snapshot comes around.
        clock = FakeClock()
        bus = MemoryBroadcastBus()
        here = make_tracker(bus=bus, clock=clock, origin="here")
        there = make_tracker(bus=bus, clock=clock, origin="there")
        there.watch("presence.orders.42")

        await here.join("presence.orders.42", 7)
        await here.join("presence.orders.42", 7)  # second tab, no snapshot yet
        roster = there.roster("presence.orders.42")
        assert [(m.user_id, m.connections) for m in roster] == [(7, 2)]

        await here.leave("presence.orders.42", 7)  # one tab closes
        roster = there.roster("presence.orders.42")
        assert [(m.user_id, m.connections) for m in roster] == [(7, 1)]

        await here.leave("presence.orders.42", 7)  # last tab
        assert there.roster("presence.orders.42") == []

    async def test_control_receipts_refresh_last_seen(self):
        # Any live control message proves the origin is alive — its stamp
        # must refresh, or a chatty-but-heartbeat-less origin ages out of
        # every roster mid-conversation.
        clock = FakeClock()
        bus = MemoryBroadcastBus()
        here = make_tracker(bus=bus, clock=clock, origin="here")
        there = make_tracker(bus=bus, clock=clock, origin="there", ghost_ttl=30.0)
        there.watch("presence.orders.42")

        await here.join("presence.orders.42", 7)
        clock.advance(31.0)  # past the ttl on the original stamp...
        await here.join("presence.orders.42", 9)  # ...yet the origin is alive
        roster = there.roster("presence.orders.42")
        assert sorted(m.user_id for m in roster) == [7, 9]

    async def test_heartbeat_survives_a_join_during_publish(self):
        # Remote receivers run inline with heartbeat's publish; anything a
        # receiver does that joins a *new* channel mutates the dict being
        # iterated. The snapshot must be taken up front.
        from fastplace.broadcasting import _CONTROL_PREFIX

        bus = MemoryBroadcastBus()
        tracker = make_tracker(bus=bus, origin="solo")
        await tracker.join("presence.a", 1)

        async def sabotage(control_channel: str, data: str) -> None:
            await tracker.join("presence.b", 2)

        bus.subscribe(f"{_CONTROL_PREFIX}presence.a", sabotage)
        await tracker.heartbeat()  # must not raise
        assert [m.user_id for m in tracker.roster("presence.b")] == [2]
