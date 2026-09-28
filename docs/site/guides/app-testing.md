# Testing your app

Every Fastplace app ships with a working test story out of the box: a
pytest plugin that auto-loads, a throwaway database so no test can ever
touch your real data, fluent HTTP assertions, fakes for mail/queue/events,
a time-freezing clock, and model factories. Run `pytest` in a scaffolded
app and it just works — no fixtures to wire, no config to write.

## The plugin

Fastplace registers a pytest plugin automatically (`fastplace.testing.plugin`,
via the `pytest11` entry point). It does two things before your first test
runs:

1. **Pins `DATABASE_URL` to a throwaway database.** By default that is a
   fresh SQLite file under pytest's tmp tree. Every ORM write in the
   session — from any test, fixture, or code path — lands there, never in
   the database your `.env` points at.
2. **Installs the fixtures** described below.

Two settings control the pin:

```ini
# pyproject.toml
[tool.pytest.ini_options]
fastplace_test_database = "auto"   # "auto" (default) | "off" | a database URL
```

```bash
# Environment override — highest precedence. Use this in CI to run the
# suite against a disposable PostgreSQL:
FASTPLACE_TEST_DATABASE_URL="postgresql://user@localhost/app_test" pytest
```

`"off"` disables the pin entirely (for suites that manage their own
databases). A URL in the ini option pins that specific database — but only
for projects that rely on the plugin's own fixtures. Scaffolded apps
(`fastplace new`) carry the pin in their `tests/conftest.py`, which honors
`FASTPLACE_TEST_DATABASE_URL` and otherwise re-pins a throwaway SQLite per
test; the ini URL never overrides the conftest's re-pin.

::: warning
The plugin never reads your `.env` on purpose — `DATABASE_URL` there *is*
the real database the pin exists to protect. Only an explicit
`FASTPLACE_TEST_DATABASE_URL` redirects the throwaway (in both the plugin
and scaffolded apps' conftest).
:::

## HTTP tests: `app` and `client`

The plugin provides minimal `app` and `client` fixtures: it imports your
`routes/web.py` (plus optional `auth.py` / `api.py` / `ai.py`) and boots
the real application over the test database.

Scaffolded apps (`fastplace new`) ship a richer pair of the same fixtures
in `tests/conftest.py` — real middleware, session + CSRF token rotation,
passkey routes — and those override the plugin's by name. Use whichever
your conftest provides; the tests below work with both.

```python
async def test_dashboard(client):
    response = await client.get("/dashboard")
    assert response.assert_ok().assert_see("Welcome")
```

### `TestResponse` assertions

`client` responses are fluent `TestResponse` objects — every method
returns the response, so assertions chain:

| Assertion | Checks |
|---|---|
| `assert_ok()` | 2xx status |
| `assert_status(code)` | exact status |
| `assert_unauthorized()` / `assert_forbidden()` / `assert_not_found()` | 401 / 403 / 404 |
| `assert_redirect_to("/login")` | 3xx + Location match (query ignored unless given) |
| `assert_validated("email")` | the 422 error envelope, optionally on a field |
| `assert_json({...})` | JSON body contains the given top-level subset |
| `assert_json_path("items.0.name", "Ada")` | dotted path resolves (and equals) — lists index by number |
| `assert_see("text")` / `assert_dont_see("text")` | body contains / lacks text |
| `assert_header("X-Foo", "bar")` | exact header value |

Failed assertions print the status and the first 500 characters of the
body, so a red test tells you *what* came back, not just that it didn't.

POST/PUT/PATCH/DELETE requests automatically carry the CSRF token the
previous response set — you never hand-roll CSRF in tests.

## Fakes

### Mail — `mail`

Pins `MAIL_DRIVER=memory` and clears the outbox around each test.
Assertions read the same outbox the memory transport writes.

```python
async def test_welcome_email(mail, client):
    await client.post("/signup", data={...})
    mail.assert_sent(to="ada@example.com", subject_contains="Welcome")
    mail.assert_sent_count(1)
    mail.assert_nothing_sent()  # fails here — one was sent
```

Filters combine: `assert_sent(to=..., subject_contains=..., body_contains=...)`,
all optional, with `times=` for an exact count. `assert_not_sent(...)`
inverts; `mail.messages()` / `mail.sent(**filters)` return the `MailMessage`
objects for deeper assertions.

### Queue — `queue_fake`

Installs an in-memory queue that records every dispatch. Jobs pushed
during a test are real `MemoryQueue` dispatches (validation, envelopes,
unique keys all apply) — only the transport is memory.

```python
async def test_signup_dispatches_followup(queue_fake, client):
    await client.post("/signup", data={...})

    queue_fake.assert_pushed("send-welcome", match={"user_id": 1})
    queue_fake.assert_not_pushed("charge-card")
    queue_fake.assert_nothing_pushed()
```

`assert_pushed(name, match=None, times=None)` — match passes if every
given key equals the pushed payload's value (extra pushed keys are fine).
`assert_executed(name, ...)` asserts the job actually *ran* after
`queue_fake.run()`; `run()` drains the queue inline (pass
`honor_sentinel=False` to ignore shutdown sentinels) and returns the
number of jobs executed.

### Events — `events_fake`

Records domain events instead of delivering them (listeners skipped):

```python
async def test_signup_dispatches_event(events_fake, client):
    await client.post("/signup", data={...})
    events_fake.assert_dispatched("user.registered")
    events_fake.assert_not_dispatched("user.deleted")
```

## Time — `clock`

`clock.freeze(moment)` stops the clock app-wide; `travel()` moves it
forward, `move_to()` jumps it. `datetime.now`, `time.time`, and friends
all follow.

```python
async def test_trial_expires(clock, client):
    clock.freeze("2026-01-01T12:00:00+00:00")
    ...  # create a trial
    clock.travel(days=15)
    ...  # the trial is now expired, everywhere
```

```python
clock.freeze("2026-01-01")  # date, datetime, or ISO string
clock.travel(hours=2, minutes=30)  # any timedelta keywords
clock.move_to("2026-06-01")  # jump, forwards or backwards
clock.now()  # the frozen moment
clock.frozen  # True while frozen (a property, no call)
```

Freeze before the code under test runs and everything — ORM timestamps,
cache TTLs, token expiry — sees the same frozen time. The fixture
requires the `testing` extra (`pip install 'fastplace[testing]'`).

## Model factories

`ModelFactory` gives test entities realistic defaults; tests override
only what they care about. Subclass per model:

```python
# tests/factories.py
from fastplace.testing import ModelFactory

from app.models import User


class UserFactory(ModelFactory):
    model = User

    async def definition(self):
        return {
            "name": "Ada",
            "email": self.sequence("user"),  # user1, user2, ... never collides
        }
```

```python
user = await UserFactory().create()  # persisted
users = await UserFactory().create_many(3)  # list of 3
draft = await UserFactory().make(name="Bob")  # unsaved instance
admin = await UserFactory().create(role="admin")  # override one field
```

`create`/`create_many` persist through the model's ordinary `save()` path,
so model events and fillable rules apply exactly as in production.

## Database isolation modes

Beyond the session-wide throwaway pin, two opt-in fixtures control
per-test isolation:

```python
async def test_repository_finds_users(
    transactional_db,
): ...  # every write joins one transaction; rolled back after the test
```

- **`transactional_db`** — the whole test runs inside a single
  transaction that is rolled back afterwards. Schema is created inside
  the transaction too, so the database file is left exactly as it was.
  Fastest option for repository/service/model tests. Not compatible with
  the `app` fixture (the held connection owns the database).
- **`clean_db`** — a fresh schema whose rows are wiped around every test.
  Use when a test needs the real request cycle (`app`/`client`) *and* a
  clean database.

Plain tests without either fixture share the session database and should
not assume a clean slate — reach for one of the two when data leaks
between tests.

Models that live outside `app/models` (scaffolded apps keep them under
`app/modules/<name>/models/`) must be named with the `fastplace_models`
marker so the fixtures import them before building the schema:

```python
import pytest


@pytest.mark.fastplace_models("app.modules.accounts.models.user")
async def test_user_repository(transactional_db): ...
```

## Scaffolding tests

```bash
fastplace make:test InvoiceTest           # tests/unit/test_invoice.py — pure unit
fastplace make:test InvoiceTest --feature # tests/feature/test_invoice.py — uses `client`
```

The unit template is sync and boots nothing; the feature template is
async and receives the `client` fixture. First run in a project without a
`tests/conftest.py` also lays down pytest config — and, when the project
has the scaffold shape (`routes/web.py` plus the accounts model), the app
conftest too. Other shapes skip the conftest and keep the plugin's own
fixtures; an existing conftest or pyproject is never overwritten.

## What the toolkit does not cover (yet)

Keep it honest: the toolkit ships no HTTP-mocking layer and no storage
fakes. For outbound HTTP in tests use
[respx](https://lundberg.github.io/respx/) with the `client` transport —
it intercepts at `httpx` level, which is where your app's outbound calls
live. For the filesystem, pytest's built-in `tmp_path` and `monkeypatch`
remain the right tools. Canonical patterns:

```python
import respx
from httpx import Response


@respx.mock
async def test_weather_widget(client):
    respx.get("https://api.weather.test/").mock(return_value=Response(200, json={"temp": 21}))
    response = await client.get("/dashboard")
    assert response.assert_ok().assert_see("21°")
```

```python
def test_report_written(monkeypatch, tmp_path):
    monkeypatch.setenv("REPORT_DIR", str(tmp_path))
    ...  # assert the file exists under tmp_path, never the real upload dir
```

If a pattern turns up repeatedly, file an issue — the fakes grow with the
framework's own drivers.
