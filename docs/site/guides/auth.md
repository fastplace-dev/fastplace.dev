# Authentication

Fastplace ships guards (who is this?), user providers (where do users
live?), and hashing — configured in `config/auth.py`, overridable by env.

## Configuration

```python
# config/auth.py
AUTH_DEFAULT_GUARD = "session"

AUTH_GUARDS = {
    "session": {"driver": "session"},  # signed cookie
    "token": {"driver": "jwt", "algorithm": "HS256", "ttl": 3600},  # stateless Bearer
}

AUTH_PROVIDERS = {
    # "dict" is the in-memory registry (tests, seeders); point real apps at a model:
    "users": {"driver": "orm", "model": "app.modules.accounts.models.User"},
}
```

## Session guard

Cookie-backed, for the bridge:

```python
from fastplace.auth.guards import SessionGuard

guard = SessionGuard(provider)
await guard.login(request, user)  # sets the signed session cookie
await guard.logout(request)
user = await guard.user(request)  # resolved per request
```

## Token guard (JWT)

Stateless Bearer tokens for the unified API:

```python
from fastplace.auth.guards import TokenGuard
from fastplace.config import config

guard = TokenGuard(
    provider,
    secret=config("APP_KEY"),
    algorithm="HS256",
    ttl=3600,
    issuer="fastplace",
)
token = guard.issue_for(user)  # claims from the provider's identifier
claims = guard.decode(token)  # raises on expiry/signature/issuer mismatch
```

Secrets come from `APP_KEY` — never commit `.env`; keep `.env.example`
authoritative for key names.

## Middleware

Register once in `config/app.py` (order matters — outermost first):

```python
MIDDLEWARE = [
    "fastplace.auth.middleware.ResolveUserMiddleware",  # request.user
    "fastplace.auth.middleware.CsrfMiddleware",  # bridge form guard
]
```

`ResolveUserMiddleware` runs the default guard and exposes `request.user`
(and `request.set_user(user)` for tests and impersonation flows).
`CsrfMiddleware` validates the token on state-changing bridge requests.

## User providers

- **dict** — in-memory registry: perfect for tests and seeders.
- **orm** — `OrmUserProvider` resolves identifiers against your model;
  lazy `raise` relationships mean an unauthenticated request never touches
  the database.

```python
from fastplace.auth.providers import OrmUserProvider

provider = OrmUserProvider("app.modules.accounts.models.User")
```

## Hashing

```python
from fastplace.auth.hashing import Hash

digest = Hash.make("password")  # scrypt by default, salted per hash
Hash.check("password", digest)  # constant-time comparison
```

Install the `pwdlib` extra (`pip install "fastplace[pwdlib]"`) for ArgUI
-grade argon2 — the hasher upgrades automatically when the extra is
present.

## Multi-tenant apps

Company-scoped apps combine the session guard with
`fastplace-tenancy`'s membership checks — see the
[tenancy guide](/guides/tenancy) for `require_membership` and the
company-context middleware.
