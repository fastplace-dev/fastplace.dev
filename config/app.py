"""Application configuration defaults (env vars always win)."""

APP_NAME = "Fastplace"
APP_ENV = "local"
# Safe by default — flip to True in .env for local debugging. The kernel also
# force-disables debug details whenever APP_ENV=production.
APP_DEBUG = False
APP_URL = "http://localhost:8000"

# Bridge + assets
VITE_DEV_URL = "http://localhost:5173"

# Middleware (dotted paths, outermost first)
MIDDLEWARE = []
