"""Authentication guard + user-provider configuration (env vars always win)."""

AUTH_DEFAULT_GUARD = "session"

# Guard drivers: "session" (server-side session store) and "jwt" (stateless Bearer token).
AUTH_GUARDS = {
    "session": {"driver": "session"},
    "token": {"driver": "jwt", "algorithm": "HS256", "ttl": 3600, "issuer": "fastplace"},
}

# How guards resolve an identifier back to a user. The default "dict" driver
# is an in-memory registry (tests/seeders). Point "users" at your model for
# real apps: {"driver": "orm", "model": "app.modules.accounts.models.User"}.
AUTH_PROVIDERS = {
    "users": {"driver": "dict"},
}
AUTH_USER_PROVIDER = "users"
