"""Framework /ws/broadcast endpoint — auth, frames, presence, eviction.

The socket layer in ``fastplace.http.broadcast_ws`` must carry the whole
contract: session-cookie authentication (the session middleware skips
websocket scope, so the handler loads the session from the handshake cookie
itself), subscribe-time channel authorization, the JSON frame protocol, the
subscription cap, presence rosters, and per-user eviction. Everything runs
against the in-process memory bus through ``get_app`` — no broker, no
network.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from fastplace.http import Router, get_app

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_broadcasting():
    """Fresh bus + authorizer chain + socket registry per test."""
    from fastplace import broadcasting
    from fastplace.http import broadcast_ws

    broadcasting.reset_broadcasting()
    broadcast_ws.reset()
    from fastplace.auth.providers import dict_provider

    users = dict(dict_provider._users)
    yield
    dict_provider._users.clear()
    dict_provider._users.update(users)
    broadcasting.reset_broadcasting()
    broadcast_ws.reset()


def _broadcast_app() -> tuple[object, Router]:
    """One app whose /push and /evict routes act inside the portal loop.

    Delivery has to be published from the same event loop as the open
    socket, so the test drives it through plain HTTP routes.
    """
    from fastplace.broadcasting import broadcast

    async def push(request):
        channel = request.query_params.get("channel", "orders.42")
        payload = request.query_params.get("payload", '{"n":1}')
        import json as _json

        await broadcast(channel, _json.loads(payload))
        return {"pushed": channel}

    async def evict(request):
        from fastplace.http import broadcast_ws

        user_id = int(request.query_params["user_id"])
        channel = request.query_params.get("channel") or None
        return {"evicted": await broadcast_ws.evict(user_id, channel=channel)}

    router = Router()
    router.get("/push", push)
    router.get("/evict", evict)
    return get_app(routes=router), router


async def _session_cookie(app, user_id) -> str:
    """Register a dict-provider user and mint a live session row."""
    from fastplace.auth.providers import dict_provider
    from fastplace.http.session import session_store  # noqa: F401 — type only

    dict_provider.add(SimpleNamespace(id=user_id))
    store = app.state.fastplace_session_store
    sid = uuid.uuid4().hex
    await store.write(sid, {"user_id": user_id})
    return f"fastplace_session={sid}"


@pytest.fixture()
def app_client(monkeypatch):
    """App + one TestClient factory that stamps a user's cookie per call.

    The default guard is pointed at the in-memory dict provider: the socket
    contract under test is session-cookie → user resolution, not the users
    table.
    """
    monkeypatch.setenv("AUTH_USER_PROVIDER", "fftest-dict")
    app, _ = _broadcast_app()

    def connect(user_id: int | None = None, **kwargs):
        client = TestClient(app)
        headers = dict(kwargs.pop("headers", {}))
        if user_id is not None:
            import asyncio

            cookie = asyncio.run(_session_cookie(app, user_id))
            headers["cookie"] = cookie
        return client.websocket_connect("/ws/broadcast", headers=headers, **kwargs)

    return app, TestClient(app), connect


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------


class TestSocketAuth:
    def test_anonymous_socket_is_closed_4401(self, app_client):
        _, _, connect = app_client
        with connect() as ws:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws.receive_json()
        assert excinfo.value.code == 4401

    def test_unknown_session_cookie_is_anonymous(self, app_client):
        _, client, _ = app_client
        with client.websocket_connect(
            "/ws/broadcast", headers={"cookie": "fastplace_session=does-not-exist"}
        ) as ws:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws.receive_json()
        assert excinfo.value.code == 4401

    def test_origin_mismatch_is_rejected(self, app_client):
        # Browsers always send Origin on a ws handshake and it must name this
        # host (CSWSH defense). Non-browser clients may omit it entirely.
        _, client, _ = app_client
        with client.websocket_connect(
            "/ws/broadcast", headers={"origin": "http://evil.example"}
        ) as ws:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws.receive_json()
        assert excinfo.value.code == 4400


# ---------------------------------------------------------------------------
# the frame protocol
# ---------------------------------------------------------------------------


class TestFrames:
    def test_subscribe_then_server_push_arrives(self, app_client):
        _, client, connect = app_client
        with connect(user_id=7) as ws:
            ws.send_json({"type": "subscribe", "channel": "orders.42"})
            assert ws.receive_json() == {"type": "subscribed", "channel": "orders.42"}
            client.get("/push", params={"channel": "orders.42", "payload": '{"n":1}'})
            assert ws.receive_json() == {
                "type": "message",
                "channel": "orders.42",
                "payload": {"n": 1},
            }

    def test_private_channel_denial_is_an_error_frame_socket_stays_open(
        self, app_client, monkeypatch
    ):
        # An explicitly empty BROADCAST_PRIVATE_ABILITY denies every private
        # channel (fail-closed) — but denial is an error frame, never a close.
        # Pinned to "" so the sample app's config default cannot flip the
        # premise: this tests the framework's no-ability branch, not the app.
        monkeypatch.setenv("BROADCAST_PRIVATE_ABILITY", "")
        _, _, connect = app_client
        with connect(user_id=7) as ws:
            ws.send_json({"type": "subscribe", "channel": "private.orders.42"})
            frame = ws.receive_json()
            assert frame["type"] == "error"
            assert frame["channel"] == "private.orders.42"
            # The socket still serves a public subscribe afterwards.
            ws.send_json({"type": "subscribe", "channel": "orders.42"})
            assert ws.receive_json()["type"] == "subscribed"

    def test_malformed_json_closes_4400(self, app_client):
        _, _, connect = app_client
        with connect(user_id=7) as ws:
            ws.send_text("not json at all")
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws.receive_json()
        assert excinfo.value.code == 4400

    def test_wrong_frame_shape_closes_4400(self, app_client):
        _, _, connect = app_client
        with connect(user_id=7) as ws:
            ws.send_json({"type": "nope", "channel": "orders.42"})
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws.receive_json()
        assert excinfo.value.code == 4400

    def test_unsubscribe_is_idempotent_and_stops_delivery(self, app_client):
        _, client, connect = app_client
        with connect(user_id=7) as ws:
            ws.send_json({"type": "subscribe", "channel": "orders.42"})
            assert ws.receive_json()["type"] == "subscribed"
            ws.send_json({"type": "unsubscribe", "channel": "orders.42"})
            assert ws.receive_json() == {"type": "unsubscribed", "channel": "orders.42"}
            # Unsubscribing again is a no-op that still acks (idempotent).
            ws.send_json({"type": "unsubscribe", "channel": "orders.42"})
            assert ws.receive_json()["type"] == "unsubscribed"
            client.get("/push", params={"channel": "orders.42"})
            ws.send_json({"type": "subscribe", "channel": "orders.43"})
            # The next frame is the new subscribe ack — no message frame for
            # orders.42 may arrive in between (frames are ordered).
            assert ws.receive_json() == {"type": "subscribed", "channel": "orders.43"}

    def test_subscription_cap_denies_with_an_error_frame(self, app_client, monkeypatch):
        monkeypatch.setenv("BROADCAST_MAX_SUBSCRIPTIONS", "2")
        _, _, connect = app_client
        with connect(user_id=7) as ws:
            for i in (41, 42):
                ws.send_json({"type": "subscribe", "channel": f"orders.{i}"})
                assert ws.receive_json()["type"] == "subscribed"
            ws.send_json({"type": "subscribe", "channel": "orders.43"})
            frame = ws.receive_json()
            assert frame["type"] == "error"
            assert "limit" in frame["error"]

    def test_ping_frames_keep_the_socket_alive(self, app_client, monkeypatch):
        monkeypatch.setenv("BROADCAST_PING_INTERVAL", "0.05")
        _, _, connect = app_client
        with connect(user_id=7) as ws:
            ws.send_json({"type": "subscribe", "channel": "orders.42"})
            assert ws.receive_json()["type"] == "subscribed"
            assert ws.receive_json()["type"] == "ping"

    def test_disabled_flag_mounts_no_route(self):
        app = get_app(config={"BROADCAST_ENABLED": False})
        paths = {getattr(route, "path", None) for route in app.routes}
        assert "/ws/broadcast" not in paths


# ---------------------------------------------------------------------------
# presence
# ---------------------------------------------------------------------------


class TestPresence:
    def test_subscribed_frame_carries_the_roster_and_joins_are_broadcast(
        self, app_client, monkeypatch
    ):
        from fastplace.authz import gate

        @gate.define("view-broadcast")
        async def view_broadcast(user, channel):
            return True

        monkeypatch.setenv("BROADCAST_PRIVATE_ABILITY", "view-broadcast")
        app, client, connect = app_client
        try:
            with connect(user_id=7) as first:
                first.send_json({"type": "subscribe", "channel": "presence.orders.1"})
                frame = first.receive_json()
                assert frame["type"] == "subscribed"
                assert frame["channel"] == "presence.orders.1"
                assert frame["members"] == [{"user_id": 7, "metadata": None, "connections": 1}]
                with connect(user_id=8) as second:
                    second.send_json({"type": "subscribe", "channel": "presence.orders.1"})
                    joined = second.receive_json()
                    assert joined["type"] == "subscribed"
                    assert {m["user_id"] for m in joined["members"]} == {7, 8}
                    # The first socket sees the roster change without asking.
                    update = first.receive_json()
                    assert update["type"] == "presence"
                    assert update["channel"] == "presence.orders.1"
                    assert {m["user_id"] for m in update["members"]} == {7, 8}
                # Second socket disconnecting leaves the roster.
                left = first.receive_json()
                assert left["type"] == "presence"
                assert [m["user_id"] for m in left["members"]] == [7]
        finally:
            gate.reset()


# ---------------------------------------------------------------------------
# eviction
# ---------------------------------------------------------------------------


class TestEviction:
    def test_evict_closes_only_the_target_users_sockets(self, app_client):
        _, client, connect = app_client
        with connect(user_id=7) as target, connect(user_id=8) as bystander:
            for ws, chan in ((target, "orders.9"), (bystander, "orders.9")):
                ws.send_json({"type": "subscribe", "channel": chan})
                assert ws.receive_json()["type"] == "subscribed"
            assert client.get("/evict", params={"user_id": 7}).json() == {"evicted": 1}
            with pytest.raises(WebSocketDisconnect) as excinfo:
                target.receive_json()
            assert excinfo.value.code == 4401
            # The bystander keeps receiving on the same channel.
            client.get("/push", params={"channel": "orders.9"})
            assert bystander.receive_json()["type"] == "message"


# ---------------------------------------------------------------------------
# presence heartbeat scheduler
# ---------------------------------------------------------------------------


class TestPresenceHeartbeat:
    async def test_first_tracker_use_starts_the_heartbeat_task(self):
        # Nobody else heartbeats: the WS layer owns the scheduler, or remote
        # rosters ghost-age every ghost_ttl even while sockets are open.
        from fastplace.http import broadcast_ws

        tracker = broadcast_ws._presence()
        assert tracker is not None
        task = broadcast_ws._heartbeat_task
        assert task is not None and not task.done()
        assert task.get_name() == "fastplace-presence-heartbeat"

    async def test_reset_cancels_the_heartbeat_task(self):
        import asyncio

        from fastplace.http import broadcast_ws

        broadcast_ws._presence()
        task = broadcast_ws._heartbeat_task
        assert task is not None
        broadcast_ws.reset()
        with pytest.raises(asyncio.CancelledError):
            await task  # cancellation lands once the loop runs the task
        assert broadcast_ws._heartbeat_task is None
