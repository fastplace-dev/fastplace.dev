"""Logging configuration — channels, rotation, correlation defaults."""

# Channel selection: "single" (one rotating file — the zero-config default),
# "daily" (midnight-rotated files, N days retention), "stderr", "null",
# "stack" (composite of LOG_STACK, e.g. files + stderr for containerized
# deploys). Anything else fails the boot loudly.
LOG_CHANNEL = "single"
LOG_LEVEL = "INFO"

# Members of the "stack" channel (comma-separated channel names).
LOG_STACK = "single,daily,stderr"

# "single" rotation: fastplace.log rolls at this size, keeping N backups
# (fastplace.log.1 … fastplace.log.N, newest = .1).
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

# "daily" retention: fastplace-YYYY-MM-DD.log files older than this are
# deleted at the next midnight rotation.
LOG_DAILY_DAYS = 7
