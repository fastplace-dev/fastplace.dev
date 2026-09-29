"""Queue configuration — background job driver selection."""

QUEUE_DRIVER = "memory"  # "memory" (dev default) | "saq"
QUEUE_NAME = "fastplace"  # SAQ queue key namespace
QUEUE_REDIS_URL = "redis://localhost:6379/0"

# SAQ web dashboard (fastplace.http.dashboard) — OFF by default; mounts only
# under QUEUE_DRIVER=saq. ABILITY None = any authenticated user; a set value
# must be a defined gate ability (undefined fails loud on first request).
QUEUE_DASHBOARD_ENABLED = False
QUEUE_DASHBOARD_PATH = "/queue-dashboard"
QUEUE_DASHBOARD_ABILITY = None
