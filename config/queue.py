"""Queue configuration — background job driver selection."""

QUEUE_DRIVER = "memory"  # "memory" (dev default) | "saq"
QUEUE_NAME = "fastplace"  # SAQ queue key namespace
QUEUE_REDIS_URL = "redis://localhost:6379/0"
