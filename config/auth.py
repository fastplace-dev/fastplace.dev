"""Authentication guard + user-provider configuration (env vars always win)."""

AUTH_DEFAULT_GUARD = "session"

# Guard drivers: "session" (server-side session store) and "jwt" (stateless Bearer token).
AUTH_GUARDS = {
    "session": {"driver": "session"},
    "token": {"driver": "jwt", "algorithm": "HS256", "ttl": 3600, "issuer": "fastplace"},
}

# How guards resolve an identifier back to a user. The sample app resolves
# through the ORM User model; the in-memory "dict" driver stays available for
# tests/seeders ({"driver": "dict"}).
AUTH_PROVIDERS = {
    "users": {
        "driver": "orm",
        "model": "app.modules.accounts.models.User",
    },
}
AUTH_USER_PROVIDER = "users"

# Login lockout (the session guard's attempt limiter): after
# AUTH_LOGIN_MAX_ATTEMPTS failed attempts for one email|ip pair, the next
# attempt is locked out until AUTH_LOGIN_DECAY seconds have elapsed.
AUTH_LOGIN_MAX_ATTEMPTS = 5
AUTH_LOGIN_DECAY = 60

# Password policy in the server dialect — Phase 2 honors the "min:N" clause
# (registration validation + the bridge pages' passwordrules translation);
# richer clauses arrive with the settings UI.
PASSWORD_RULES = "min:8"

# Password reset + email verification (Phase 3)
AUTH_PASSWORD_EXPIRE = 60  # minutes a reset token stays live
AUTH_RESET_THROTTLE = 60  # seconds between reset-link emails per address
TRUSTED_HOSTS: list[str] = []  # hosts allowed to name the origin when APP_URL is empty

# Seconds a password confirmation stays valid (spec §4.12) — three hours.
PASSWORD_TIMEOUT = 10800

# Feature flag: the two-factor management endpoints + UI (Phase 4).
TWO_FACTOR_ENABLED = True

# Abilities precomputed into every page payload's shared auth props (§4.16).
# Zero-extra-arg abilities only — no model instance exists at props time.
# Env override is comma-separated: AUTH_SHARED_ABILITIES=view-posts,view-profile
AUTH_SHARED_ABILITIES: list[str] = []

# Passkeys (WebAuthn) — the framework routes /user/passkeys* and
# /passkeys/* when enabled (spec: framework-owned, zero app code).
# Needs the 'webauthn' extra: pip install 'fastplace[webauthn]'.
# Behind a proxy APP_URL must be the public origin (TRUSTED_HOSTS convention).
AUTH_PASSKEYS = {
    "enabled": True,  # env: APP_PASSKEYS_ENABLED
    "rp_name": None,  # default: APP_NAME
    "rp_id": None,  # default: APP_URL host
    "origins": None,  # default: [APP_URL]
    "timeout_ms": 60000,  # env: APP_PASSKEYS_TIMEOUT_MS
    "user_verification": "preferred",  # confirm ALWAYS requires UV
    "attestation": "none",  # enterprise attestation lands later
    "challenge_ttl": 300,  # env: APP_PASSKEYS_CHALLENGE_TTL
    "login_max_attempts": 5,  # env: APP_PASSKEYS_LOGIN_MAX_ATTEMPTS
}
