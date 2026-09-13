"""Application configuration defaults (env vars always win)."""

APP_NAME = "Fastplace"
APP_ENV = "local"
# Safe by default — flip to True in .env for local debugging. The kernel also
# force-disables debug details whenever APP_ENV=production.
APP_DEBUG = False
APP_URL = "http://localhost:8000"

# Bridge + assets
VITE_DEV_URL = "http://localhost:5173"

# Signing secret for sessions/CSRF/tokens. Empty in the repo — set in .env.
# Without it, local sessions use an ephemeral per-process key (fine for dev,
# invalid across restarts/workers); JWTs refuse to issue (see config/auth.py).
# Production refuses to boot without it (kernel fails fast).
APP_KEY = ""

# Signed-cookie session (kernel installs Starlette SessionMiddleware)
SESSION_COOKIE = "fastplace_session"
SESSION_LIFETIME = 7200  # seconds

# CSRF protection (fastplace.auth.middleware.CsrfMiddleware)
CSRF_EXCEPT: list[str] = []

# Default HTTP middleware stack (dotted paths, outermost first)
MIDDLEWARE = [
    "fastplace.auth.middleware.ResolveUserMiddleware",
    "fastplace.auth.middleware.CsrfMiddleware",
]
