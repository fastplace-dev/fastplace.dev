# Broadcasting

Server-push over WebSocket channels, behind one `broadcast()` API. The
kernel mounts a framework-owned endpoint at `/ws/broadcast`
(`BROADCAST_ENABLED=false` mounts nothing), authorization happens at
**subscribe time**, and drivers swap behind `BROADCAST_DRIVER` — `memory`
(default, single-process, deterministic in dev and tests) or `redis`
(pub/sub fan-out across workers; reuses the queue extra's redis).

## Publishing

```python
from fastplace.broadcasting import broadcast

await broadcast("orders.42", {"status": "shipped"})
```

The payload is JSON-serialized once at this boundary and capped by
`BROADCAST_MAX_PAYLOAD_BYTES`. Publishing needs no user — subscribe-time
authorization is the security boundary.

## Channel taxonomy

The leading segments of a dot-namespaced channel decide who may subscribe:

| Channel | Rule |
| --- | --- |
| `orders.42` | Public — any connected socket. |
| `private.orders.42` | Authenticated user plus a gate ability. |
| `presence.orders.42` | Private rules plus a live roster. |
| `company.7.orders.1` | Tenancy-scoped — membership row decides (below). |

Private and presence channels fail closed: `BROADCAST_PRIVATE_ABILITY`
names a gate ability your app defines, and **leaving it unset denies
everyone** — "private but the ability name is missing" must never degrade
to public.

```python
from fastplace.authz import gate


@gate.define("view-broadcast")
async def view_broadcast(user, channel):
    return user is not None  # your policy, per parsed channel
```

## The WebSocket endpoint

Connect to `/ws/broadcast` with the session cookie your app already
mints — the handshake resolves the user from it (anonymous sockets close
`4401`). Browsers must send a matching `Origin` (cross-site WebSocket
hijacking defense); non-browser clients may omit it.

Frames are JSON. The client sends:

```json
{"type": "subscribe", "channel": "orders.42"}
{"type": "unsubscribe", "channel": "orders.42"}
```

The server answers `subscribed` / `unsubscribed` acks, `message` frames
carrying the deserialized payload, `presence` roster updates, `ping`
keepalives (`BROADCAST_PING_INTERVAL`), and `error` frames — a denied
subscribe is an error frame, never a close; the socket stays usable:

```json
{"type": "message", "channel": "orders.42", "payload": {"status": "shipped"}}
{"type": "error", "channel": "private.orders.42", "error": "unauthorized"}
```

Malformed frames close `4400`. Per-socket bounds: at most
`BROADCAST_MAX_SUBSCRIPTIONS` channels, and a bounded outbound queue — a
slow consumer is closed (`1013`), never allowed to balloon process memory.

### Revocation

Subscribe-time checks cannot reach sockets already open. The documented
bound: when a permission is revoked, call `evict` and the affected
sockets close `4401`; clients reconnect and re-request their channels.

```python
from fastplace.http.broadcast_ws import evict

await evict(user_id)  # every socket of that user
await evict(user_id, channel="...")  # just one channel's sockets
```

## Presence

`presence.<name>` channels maintain a derived roster — no separate
roster store. The `subscribed` frame carries the current `members`
(`user_id`, optional `metadata`, `connection` count), and every join or
leave pushes a `presence` frame to the channel:

```json
{"type": "presence", "channel": "presence.orders.1",
 "members": [{"user_id": 7, "metadata": null, "connections": 1}]}
```

Member information is deliberately minimal: user id plus a small optional
metadata dict. Disconnects leave automatically; a periodic heartbeat
re-announce bounds ghost members left by crashed processes.

## Domain events → channels

The bridge is **opt-in per event**. Register an explicit name → channel
template map and only those events broadcast; nothing else ever reaches a
channel:

```python
from fastplace.broadcasting import broadcast_events

broadcast_events({"order_created": "orders.{order_id}"})
```

A mapped event rides the same post-commit buffer as the queue leg:
inside `db.transaction()` the broadcast lands only after the commit, and
a rollback discards it. A template referencing a key the payload lacks
fails loud at dispatch — that is a wiring bug, not a runtime condition.

## Multi-tenancy

With `fastplace-tenancy`, company channels are membership-checked at
subscribe time:

```python
from fastplace_tenancy import company_context, install_tenant_broadcasting, tenant_channel

install_tenant_broadcasting()  # once at boot

async with company_context(company.id):
    channel = tenant_channel("orders.1")  # "company.<id>.orders.1"

await broadcast(channel, {"status": "shipped"})
```

Non-members — including anonymous sockets — are denied; plain channels
are untouched by the tenancy rules.

## Drivers

- **memory** — in-process. Browsers served by this process receive
  everything; a broadcast from a queue worker reaches no browser (the
  driver warns instead of pretending). Right for dev and tests.
- **redis** — pub/sub fan-out via `BROADCAST_REDIS_URL` (defaults to
  `QUEUE_REDIS_URL`; requires the queue extra's redis). Multi-worker
  deployments need this — or a sticky-session load balancer with memory
  and only one worker per channel publisher, which is usually not what
  you want.

Redis pub/sub is at-most-once and unordered across processes; the
in-process driver preserves per-channel order. Message replay and
durability are deliberate omissions — a browser that missed a frame
refetches state on reconnect.

## Configuration

| Key | Default | Meaning |
| --- | --- | --- |
| `BROADCAST_ENABLED` | `true` | Mounts `/ws/broadcast` when true. |
| `BROADCAST_DRIVER` | `memory` | `memory` or `redis`. |
| `BROADCAST_REDIS_URL` | `QUEUE_REDIS_URL` | Redis for the pub/sub driver. |
| `BROADCAST_CHANNEL_PREFIX` | `fastplace:broadcast:` | Redis key namespace. |
| `BROADCAST_PRIVATE_ABILITY` | *(unset)* | Gate ability for private/presence. Unset denies everyone. |
| `BROADCAST_MAX_PAYLOAD_BYTES` | `65536` | Per-payload serialization cap. |
| `BROADCAST_MAX_SUBSCRIPTIONS` | `100` | Per-socket channel cap. |
| `BROADCAST_SOCKET_QUEUE_SIZE` | `256` | Per-socket outbound queue bound. |
| `BROADCAST_PING_INTERVAL` | `30` | Seconds between keepalive pings. |
