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

## Passkeys

Passkeys (WebAuthn) ship three ceremonies, all framework-owned: manage
(register, list, delete), usernameless login with discoverable credentials,
and a passkey alternative to password confirmation. Install the extra
first — without it the endpoints answer 501 with the install message:

```bash
pip install "fastplace[webauthn]"
```

Enable them in `config/auth.py` — the framework mounts the routes itself;
your app writes zero route code:

```python
# config/auth.py
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
```

`rp_id`/`origins` derive from `APP_URL`; behind a proxy `APP_URL` must be
the public origin (the `TRUSTED_HOSTS` convention).

The auto-mounted routes:

| Route | Middleware | Behavior |
|---|---|---|
| `GET /user/passkeys/options` | auth, verified | creation options for the current user |
| `POST /user/passkeys` | auth, verified | `{name, credential}` → register |
| `DELETE /user/passkeys/{id}` | auth, verified | delete own credential |
| `GET /passkeys/login/options` | guest | assertion options, empty allowList (discoverable credentials) |
| `POST /passkeys/login` | guest | `{credential}` → session login → `{redirect: ...}` payload |
| `GET /passkeys/confirm/options` | auth | assertion options scoped to the user's credentials |
| `POST /passkeys/confirm` | auth | `{credential}` → `password_confirmed_at` stamp |

Everything routes through `passkey_guard()` — the accessor mirrors
`guard()` — so a page controller or API surface can call the same
ceremonies the routes call:

```python
from fastplace.auth.passkey_guard import passkey_guard

guard = passkey_guard()

options = await guard.registration_options(request, user)  # creation options JSON
row_id = await guard.register(request, user, "Chrome on Mac", credential)
keys = await guard.list_for(user)  # id/name/authenticator/*_at_diff props
guard.delete(request, user, row_id)  # owner-filtered, bool

options = await guard.login_options(request)  # guest — discoverable credentials
user = await guard.login(request, credential)  # user, or None when 2FA parked

options = await guard.confirm_options(request, user)
ok = await guard.confirm(request, user, credential)  # stamps password_confirmed_at
```

Every method validates the ceremony against the stored session challenge;
`login()` resolves the user through your configured provider and finishes
with the session guard, so a 2FA-enabled user still faces the TOTP
challenge — the passkey replaces the password factor, never the second
factor. `confirm()` always demands user verification (biometric or PIN)
regardless of the configured `user_verification`.

Security notes:

- **Challenges** are session-bound, single-use, TTL-bounded (`challenge_ttl`)
  and namespaced by ceremony — a login challenge cannot verify a confirm or
  a registration.
- **Replay rejection**: when the stored `sign_count` is greater than zero
  and an assertion reports a counter at or below it, the assertion is
  rejected as a possible clone (counterless authenticator pairs skip).
- **One generic failure** — every verification failure (unknown credential,
  expired challenge, bad signature, replay) raises the same message,
  `"Unable to verify this passkey."`, so responses never leak why.
- **Owner scoping** — delete, confirm, and registration-option scopes are
  always filtered by the current user's identifier.
- Events (`passkey.registered`, `passkey.deleted`, `passkey.login`,
  `passkey.confirm`) dispatch through the normal event pipeline for audit.

## Multi-tenant apps

Company-scoped apps combine the session guard with
`fastplace-tenancy`'s membership checks — see the
[tenancy guide](/guides/tenancy) for `require_membership` and the
company-context middleware.
