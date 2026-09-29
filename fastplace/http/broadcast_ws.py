"""The broadcast WebSocket endpoint — ``/ws/broadcast``.

The socket half of the broadcasting layer (blueprint §Broadcasting): one
framework-owned endpoint carrying every channel. Authentication is the
server session cookie — the session middleware only wraps http scopes, so
the handler loads the session from the handshake cookie itself via
:func:`~fastplace.http.session.middleware.load_session_from_scope` (the
same parser, never a second one). Anonymous sockets are accepted and then
closed ``4401`` (accept-then-close, so clients observe the code); a
malformed frame closes ``4400`` — those two are the only closes. Every
other refusal (channel denied, cap reached, bad channel name) is an
``error`` frame and the socket stays open.

Wire protocol (JSON text frames):

- client → server: ``{"type": "subscribe"|"unsubscribe", "channel": "…"}``
- server → client: ``{"type": "subscribed"|"unsubscribed"|"message"|
  "presence"|"error"|"ping", "channel": "…", …}`` — presence subscribes
  carry ``members`` (the derived roster) in the ack and on every roster
  change; ``message`` frames carry the deserialized ``payload``.

Delivery rides the process broadcast bus (:func:`broadcast_bus`) — one bus
subscription per socket per channel, never a direct-call shortcut, so the
redis driver fans out across workers for free. Each socket owns a bounded
outbound queue drained by a writer task that doubles as the ping cycle; a
full queue (slow consumer) closes that one socket, never the process.
Per-user eviction (:func:`evict`) force-closes a user's sockets after a
permission revocation — subscribe-time authorization plus this eviction
bound is the documented revocation contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.websockets import WebSocketDisconnect

from fastplace.broadcasting import (
    BroadcastError,
    Member,
    PresenceTracker,
    authorize_subscribe,
    broadcast_bus,
    parse_channel,
)
from fastplace.config import config
from fastplace.http.websocket import WebSocket

__all__ = [
    "BROADCAST_WS_PATH",
    "broadcast_socket",
    "evict",
    "reset",
]

logger = logging.getLogger("fastplace.broadcasting")

#: Where the endpoint mounts (kernel wires it behind BROADCAST_ENABLED).
BROADCAST_WS_PATH = "/ws/broadcast"

#: Close codes — the only two the endpoint ever sends.
WS_CLOSE_MALFORMED = 4400
WS_CLOSE_UNAUTHENTICATED = 4401

#: Close code for a slow consumer dropped by queue overflow (RFC 8361-era
#: "try again later" semantics — the client may reconnect and resubscribe).
WS_CLOSE_SLOW_CONSUMER = 1013

_DEFAULT_MAX_SUBSCRIPTIONS = 100
_DEFAULT_QUEUE_SIZE = 256
_DEFAULT_PING_INTERVAL = 30.0


class _MalformedFrame(Exception):
    """A frame the protocol cannot parse — the socket closes 4400."""


class _Socket:
    """One connected client: its user, channels, and outbound queue."""

    def __init__(self, ws: WebSocket, user: Any, user_id: Any) -> None:
        self.ws = ws
        self.user = user
        self.user_id = user_id
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=int(config("BROADCAST_SOCKET_QUEUE_SIZE", default=_DEFAULT_QUEUE_SIZE))
        )
        self.ping_interval = float(
            config("BROADCAST_PING_INTERVAL", default=_DEFAULT_PING_INTERVAL)
        )
        # Logical channel -> bus unsubscribe handle (one per channel).
        self.unsubscribes: dict[str, Callable[[], None]] = {}
        self.presence_channels: set[str] = set()
        self.writer: asyncio.Task[None] | None = None
        self.finished = False


# Process-wide socket registry (one per worker process — evict() and roster
# pushes are local; the bus carries everything cross-process).
_sockets: set[_Socket] = set()
_by_user: dict[Any, set[_Socket]] = {}
_tracker: PresenceTracker | None = None


def reset() -> None:
    """Clear the socket registry and tracker (test seam)."""
    _sockets.clear()
    _by_user.clear()
    global _tracker
    _tracker = None


# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------


async def broadcast_socket(ws: WebSocket) -> None:
    """The framework WebSocket handler mounted at ``BROADCAST_WS_PATH``."""
    scope = ws.starlette.scope

    # Cross-site WebSocket hijacking defense: a browser always sends Origin
    # on a ws handshake and it must name this host. Non-browser clients may
    # omit it — there is nothing to spoof without a cookie-owning browser.
    if not _origin_allowed(scope):
        await ws.accept()
        await ws.close(WS_CLOSE_MALFORMED)
        return

    session = await _load_handshake_session(scope)
    user = await _resolve_user(session)
    if user is None:
        # Accept-then-close so the client observes the 4401 code (a bare
        # pre-accept close surfaces as an HTTP-level rejection instead).
        await ws.accept()
        await ws.close(WS_CLOSE_UNAUTHENTICATED)
        return

    from fastplace.auth.providers import user_identifier

    await ws.accept()
    socket = _Socket(ws, user, user_identifier(user))
    _sockets.add(socket)
    _by_user.setdefault(socket.user_id, set()).add(socket)
    socket.writer = asyncio.create_task(_writer_loop(socket), name="fastplace-broadcast-socket")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                await _handle_frame(socket, raw)
            except _MalformedFrame:
                await ws.close(WS_CLOSE_MALFORMED)
                return
    except WebSocketDisconnect:
        pass  # the client went away — cleanup below
    finally:
        await _teardown(socket)


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------


async def _handle_frame(socket: _Socket, raw: str) -> None:
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        raise _MalformedFrame from None
    if not isinstance(frame, dict):
        raise _MalformedFrame
    kind = frame.get("type")
    channel = frame.get("channel")
    if not isinstance(channel, str) or not channel:
        raise _MalformedFrame
    if kind == "subscribe":
        await _subscribe(socket, channel)
    elif kind == "unsubscribe":
        await _unsubscribe(socket, channel)
    else:
        raise _MalformedFrame


async def _subscribe(socket: _Socket, channel: str) -> None:
    # Idempotent: a repeat subscribe just re-acks — it must not double the
    # bus subscription or the presence connection count.
    if channel in socket.unsubscribes:
        await _enqueue(socket, {"type": "subscribed", "channel": channel})
        return

    try:
        parsed = parse_channel(channel)
    except BroadcastError as exc:
        await _enqueue(socket, {"type": "error", "channel": channel, "error": str(exc)})
        return

    limit = int(config("BROADCAST_MAX_SUBSCRIPTIONS", default=_DEFAULT_MAX_SUBSCRIPTIONS))
    if len(socket.unsubscribes) >= limit:
        await _enqueue(
            socket,
            {
                "type": "error",
                "channel": channel,
                "error": f"subscription limit of {limit} channels reached",
            },
        )
        return

    if not await authorize_subscribe(socket.user, parsed):
        # Denial is an error frame, never a close — a denied channel must
        # not take the socket's other subscriptions down with it.
        await _enqueue(socket, {"type": "error", "channel": channel, "error": "unauthorized"})
        return

    if parsed.kind == "presence":
        tracker = _presence()
        await tracker.join(channel, socket.user_id)
        socket.presence_channels.add(channel)
        members = [_member_dict(m) for m in tracker.roster(channel)]
        await _enqueue(socket, {"type": "subscribed", "channel": channel, "members": members})
    else:
        await _enqueue(socket, {"type": "subscribed", "channel": channel})

    # Delivery only through the bus — the same path a cross-process publish
    # takes; no in-process shortcut would survive the redis driver.
    socket.unsubscribes[channel] = broadcast_bus().subscribe(channel, _delivery(socket))


async def _unsubscribe(socket: _Socket, channel: str) -> None:
    unsubscribe = socket.unsubscribes.pop(channel, None)
    if unsubscribe is not None:
        unsubscribe()
        if channel in socket.presence_channels:
            socket.presence_channels.discard(channel)
            await _presence().leave(channel, socket.user_id)
    # Idempotent ack: unsubscribing a channel never subscribed still acks.
    await _enqueue(socket, {"type": "unsubscribed", "channel": channel})


def _delivery(socket: _Socket) -> Callable[[str, str], Any]:
    """The bus callback that fans one channel's traffic into this socket."""

    async def deliver(channel: str, data: str) -> None:
        await _enqueue(
            socket,
            {"type": "message", "channel": channel, "payload": json.loads(data)},
        )

    return deliver


async def _enqueue(socket: _Socket, frame: dict[str, Any]) -> None:
    if socket.finished:
        return
    try:
        socket.queue.put_nowait(frame)
    except asyncio.QueueFull:
        # A slow consumer is dropped — its queue is bounded so one stalled
        # client can never balloon process memory; every other socket and
        # the channel's bus subscription are unaffected.
        logger.warning(
            "broadcast socket for user %r overflowed its outbound queue; closing",
            socket.user_id,
        )
        await _teardown(socket)
        try:
            await socket.ws.close(WS_CLOSE_SLOW_CONSUMER)
        except Exception:  # already gone — closing is best-effort
            pass


async def _writer_loop(socket: _Socket) -> None:
    """Drain the outbound queue; an idle timeout sends a ping frame."""
    try:
        while True:
            try:
                frame = await asyncio.wait_for(socket.queue.get(), timeout=socket.ping_interval)
            except TimeoutError:
                frame = {"type": "ping"}
            await socket.ws.send_json(frame)
    except asyncio.CancelledError:
        raise
    except Exception:
        # The client vanished under us — the receive loop notices too and
        # runs teardown; the writer just stops.
        logger.debug("broadcast socket writer stopped", exc_info=True)


async def _teardown(socket: _Socket) -> None:
    """Release every resource this socket holds (idempotent)."""
    if socket.finished:
        return
    socket.finished = True
    for _, unsubscribe in list(socket.unsubscribes.items()):
        unsubscribe()
    socket.unsubscribes.clear()
    for channel in list(socket.presence_channels):
        try:
            await _presence().leave(channel, socket.user_id)
        except Exception:  # teardown never fails on roster plumbing
            logger.debug("presence leave during teardown failed", exc_info=True)
    socket.presence_channels.clear()
    _sockets.discard(socket)
    peers = _by_user.get(socket.user_id)
    if peers is not None:
        peers.discard(socket)
        if not peers:
            _by_user.pop(socket.user_id, None)
    if socket.writer is not None and not socket.writer.done():
        socket.writer.cancel()


# ---------------------------------------------------------------------------
# eviction
# ---------------------------------------------------------------------------


async def evict(user_id: Any, channel: str | None = None) -> int:
    """Force-close a user's sockets (optionally just one channel's).

    Subscribe-time authorization cannot reach sockets already open — this is
    the documented bound: a permission revocation calls ``evict`` and the
    affected sockets close 4401; clients reconnect and re-request their
    channels under the fresh verdict. Returns the number of closed sockets.
    """
    count = 0
    for socket in list(_by_user.get(user_id, ())):
        if channel is not None and channel not in socket.unsubscribes:
            continue
        await _teardown(socket)
        try:
            await socket.ws.close(WS_CLOSE_UNAUTHENTICATED)
        except Exception:  # already gone — closing is best-effort
            pass
        count += 1
    return count


# ---------------------------------------------------------------------------
# presence plumbing
# ---------------------------------------------------------------------------


def _presence() -> PresenceTracker:
    """The process presence tracker, rebuilt with the roster push on demand."""
    global _tracker
    if _tracker is None:
        _tracker = PresenceTracker(on_change=_push_roster)
    return _tracker


def _push_roster(channel: str, members: list[Member]) -> None:
    """Send a roster change to every local socket on that channel."""
    frame = {
        "type": "presence",
        "channel": channel,
        "members": [_member_dict(m) for m in members],
    }
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # no loop — nothing to deliver through
        return
    for socket in list(_sockets):
        if channel in socket.unsubscribes:
            loop.create_task(_enqueue(socket, frame))


def _member_dict(member: Member) -> dict[str, Any]:
    return {
        "user_id": member.user_id,
        "metadata": dict(member.metadata) if member.metadata else None,
        "connections": member.connections,
    }


# ---------------------------------------------------------------------------
# handshake authentication
# ---------------------------------------------------------------------------


def _origin_allowed(scope: dict) -> bool:
    headers = Headers(scope=scope)
    origin = headers.get("origin")
    host = headers.get("host")
    if not origin:
        return True  # non-browser client — nothing to spoof
    if not host:
        return False
    return _strip_default_port(urlsplit(origin).netloc.lower()) == _strip_default_port(host.lower())


def _strip_default_port(netloc: str) -> str:
    for suffix in (":80", ":443"):
        if netloc.endswith(suffix):
            return netloc[: -len(suffix)]
    return netloc


async def _load_handshake_session(scope: dict) -> Any:
    from fastplace.http.session import session_store
    from fastplace.http.session.middleware import load_session_from_scope

    # Reuse the app's own store instance (stashed at install) — a fresh
    # memory store would orphan every session the middleware wrote.
    app_state = getattr(scope.get("app"), "state", None)
    store = getattr(app_state, "fastplace_session_store", None) if app_state else None
    if store is None:
        store = session_store()
    cookie_name = str(config("SESSION_COOKIE", default="fastplace_session"))
    return await load_session_from_scope(store, cookie_name, scope)


async def _resolve_user(session: Any) -> Any:
    """Resolve the handshake session's user through the default guard.

    Remember-cookie resurrection is http-only by design: the fallback logs
    in and rotates cookies, which needs a response to carry them — a
    websocket handshake that must not gain side effects.
    """
    from fastplace.auth.guards import SessionGuard, guard

    identifier = session.get(SessionGuard.SESSION_KEY)
    if identifier is None:
        return None
    return await guard().provider.resolve(identifier)
