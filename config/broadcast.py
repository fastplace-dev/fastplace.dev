"""Broadcast configuration — channel fan-out driver selection."""

BROADCAST_ENABLED = True  # master switch for the /ws/broadcast endpoint
BROADCAST_DRIVER = "memory"  # "memory" (dev default) | "redis" (cross-process)
# Broker URL for the redis driver; empty/None falls back to QUEUE_REDIS_URL
# so one redis serves both subsystems unless split deliberately.
BROADCAST_REDIS_URL = None
# Every redis channel name hides behind this prefix — pub/sub is
# instance-global on the broker (the CACHE_PREFIX pattern).
BROADCAST_CHANNEL_PREFIX = "fastplace:broadcast:"
# Gate ability required to subscribe private/presence channels; None =
# private and presence channels deny everyone (fail-closed by design).
BROADCAST_PRIVATE_ABILITY = None
# Serialized payload cap in bytes; oversized payloads fail loud at publish.
BROADCAST_MAX_PAYLOAD_BYTES = 65536
