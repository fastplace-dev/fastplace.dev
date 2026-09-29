# Upgrading

How to move a Fastplace application to a newer framework release — and what
the framework will (and will never) touch in your project.

## Who owns what

Every file `fastplace new` and `fastplace make:*` emits is **yours** from the
moment it is written:

- The framework never modifies, rewrites, or deletes scaffolded files on
  upgrade. There is no hidden post-install hook, no in-place migration of
  your code. `pip install -U fastplace` changes the framework, nothing else.
- Scaffold commands skip files that already exist (they print `exists` and
  move on), so re-running `make:auth` on a project you have edited never
  clobbers your work — it only fills in files that are missing.
- The flip side: when the framework changes shape, your scaffolded files do
  not update themselves. The procedures below are how you reconcile the two.

## The standard upgrade

For most releases the whole upgrade is four commands, run from your project
root:

```bash
# 1. Bump the Python pin (or edit pyproject.toml by hand to the exact version)
pip install -U "fastplace[queue,webauthn]"

# 2. Bump the npm pins — python and npm release in lockstep (see matrix below)
npm install -D @fastplace/react@^0.3.1 @fastplace/ai-react@^0.3.1

# 3. Apply any new migrations the release ships
fastplace migrate

# 4. Run your test suite before touching anything else
pytest
```

If step 4 is green, you are done. If it is not, the changelog section for the
release names the breaking change and this guide's release notes below carry
the fix.

## Bringing a 0.1.x app to 0.2.0

0.2.0 adds the full auth starter kit. A 0.1.x project that wants it needs
three changes — each one verified against a real 0.1.0 scaffold upgraded to
the 0.2.0 wheel:

**1. Add `email-validator` to your dependencies.** 0.1.x scaffolds predate
the auth request validators, which use pydantic `EmailStr`. Without the
package the app fails at import time with
`ImportError: email-validator is not installed`. Add it next to `fastplace`
in `pyproject.toml`:

```toml
dependencies = [
    "email-validator>=2.0",
    "fastplace[queue,webauthn]>=0.2.0",
]
```

**2. Scaffold the auth surface.** This emits the controllers, requests,
routes, and jobs — skipping every file you already have:

```bash
fastplace make:auth
fastplace migrate
```

**3. Register the route middleware aliases.** 0.1.x `config/app.py` predates
the `ROUTE_MIDDLEWARE` registry. The scaffolded auth routes name aliases at
declaration time and the kernel resolves them eagerly at mount, so boot fails
with:

```
ConfigurationError: unknown route middleware alias 'guest' — register it in
ROUTE_MIDDLEWARE (config/app.py) or get_app(route_middleware=...)
```

Paste this block into your `config/app.py` (it is byte-identical to what a
fresh 0.2.0 scaffold ships):

```python
# Per-route middleware aliases: alias -> "dotted.path.ToMiddleware".
# Parameterized aliases parse as "name:arg1,arg2" at route declaration.
ROUTE_MIDDLEWARE = {
    "auth": "fastplace.auth.middleware.AuthenticateMiddleware",
    "guest": "fastplace.auth.middleware.GuestMiddleware",
    "verified": "fastplace.auth.middleware.EnsureEmailVerifiedMiddleware",
    "password.confirm": "fastplace.auth.middleware.EnsurePasswordConfirmedMiddleware",
    "throttle": "fastplace.ratelimit.ThrottleMiddleware",
    "abilities": "fastplace.auth.middleware.AbilitiesMiddleware",
    "ability": "fastplace.auth.middleware.AbilityMiddleware",
    "can": "fastplace.authz.middleware.CanMiddleware",
}
```

Finally, bump the npm pins to the matching `^0.2.0` (matrix below) and open
`/register` — the **first** account created becomes the admin.

## When your scaffolded code has drifted

You own the scaffold, so after weeks of edits a release can conflict with
files you have changed. Do not copy a fresh scaffold over your project.
Instead, diff against a throwaway reference:

```bash
# Emit a pristine copy of the CURRENT starter kit next to your project
cd "$(dirname "$PWD")"
fastplace new upgrade_reference --auth --no-install

# See exactly what the framework would emit today vs. what you carry
diff -ru --exclude=node_modules --exclude=.venv \
    upgrade_reference/app your-project/app
diff -ru upgrade_reference/config your-project/config

# Then delete the reference — it exists only for the diff
rm -rf upgrade_reference
```

Port only the changes you actually want, one file at a time, keeping your
edits. For the auth surface specifically, `fastplace make:auth` already
re-emit-and-skip: anything you have not touched updates to current, anything
you have touched stays yours.

## Python / npm version matrix

The Python package and both npm packages release together under one version —
the bridge protocol between them is only tested in that combination. The
scaffold pins the npm packages automatically from the installed `fastplace`
version, so a standard upgrade keeps them aligned without manual bookkeeping.

| fastplace (PyPI) | @fastplace/react | @fastplace/ai-react |
| ---------------- | ---------------- | ------------------- |
| 0.1.0            | 0.1.0            | 0.1.0               |
| 0.2.0            | 0.2.0            | 0.2.0               |
| 0.2.1            | 0.2.1            | 0.2.1               |
| 0.3.0            | 0.3.0            | 0.3.0               |
| 0.3.1            | 0.3.1            | 0.3.1               |

Mixing versions across the bridge is unsupported: pick one row.

For what a version bump is allowed to break, see [Versioning](./versioning.md).
