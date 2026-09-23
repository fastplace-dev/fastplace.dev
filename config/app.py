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

# Server-side session cookie (kernel installs ServerSessionMiddleware)
SESSION_COOKIE = "fastplace_session"
SESSION_LIFETIME = 7200  # seconds

# Server-side session driver: "database" (production default), "redis", or
# "memory" (local dev/tests). Unset resolves per environment — see
# fastplace/http/session/__init__.py:session_store.
SESSION_DRIVER = ""
# Cookie attributes (path/domain default to the whole app; set SESSION_DOMAIN
# for cross-subdomain sessions).
SESSION_DOMAIN = ""
SESSION_PATH = "/"

# Per-route middleware aliases: {"alias": "dotted.path.ToMiddleware"}.
# Parameterized aliases parse as "name:arg1,arg2" at route declaration.
# The credential endpoints (routes/auth.py) name these at declaration time
# and the kernel resolves them eagerly at mount — the registry must ship in
# the same commit as any route that names an alias, or boot fails.
ROUTE_MIDDLEWARE = {
    "auth": "fastplace.auth.middleware.AuthenticateMiddleware",
    "guest": "fastplace.auth.middleware.GuestMiddleware",
    "verified": "fastplace.auth.middleware.EnsureEmailVerifiedMiddleware",
    "password.confirm": "fastplace.auth.middleware.EnsurePasswordConfirmedMiddleware",
    "throttle": "fastplace.ratelimit.ThrottleMiddleware",
    "can": "fastplace.authz.middleware.CanMiddleware",
}

# CSRF protection (fastplace.auth.middleware.CsrfMiddleware)
CSRF_EXCEPT: list[str] = []

# Default HTTP middleware stack (dotted paths, outermost first). App-owned
# middleware lives in app/http/middleware/ — framework and app entries share
# one stack.
MIDDLEWARE = [
    "app.http.middleware.request_timing.RequestTimingMiddleware",
    "fastplace.auth.middleware.ResolveUserMiddleware",
    "fastplace.auth.middleware.SharedAbilitiesMiddleware",
    "fastplace.auth.middleware.CsrfMiddleware",
]
