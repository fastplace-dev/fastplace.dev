"""Mail subsystem configuration (env vars always win)."""

MAIL_DRIVER = "log"  # log | memory | smtp
MAIL_FROM_ADDRESS = "fastplace@localhost"
# Display name for the outbound sender ("Name <address>"); None = bare address.
MAIL_FROM_NAME = None
MAIL_HOST = "127.0.0.1"
MAIL_PORT = 25
MAIL_USERNAME = None
MAIL_PASSWORD = None
# "none" | "tls" (implicit TLS) | "starttls"
MAIL_ENCRYPTION = "none"
