"""Cache configuration — driver selection and store defaults."""

CACHE_DRIVER = "memory"  # "memory" (dev default) | "redis" | "database"
CACHE_TTL = 3600  # default seconds for cache().remember() when no ttl given
# Redis keys live in a shared DB (the SAQ queue defaults to the same one) —
# every cache key is prefixed with this namespace and flush() deletes only it.
CACHE_PREFIX = "fastplace:cache:"
REDIS_URL = "redis://localhost:6379/0"
