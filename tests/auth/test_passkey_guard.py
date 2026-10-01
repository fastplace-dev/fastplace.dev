"""PasskeyGuard tests — manage/login/confirm ceremonies over SessionGuard.

The guard is exercised directly (no HTTP): a fake request carries a plain
dict session and a fixed client IP, the ES256 simulator produces real
ceremony payloads, and the default guard resolves users through the dict
provider the same way tests/auth/conftest.py pins it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fastplace.auth.passkey_guard import PasskeyGuard, _login_keys
from fastplace.auth.passkeys import PasskeyStore
from fastplace.auth.providers import dict_provider
from fastplace.auth.webauthn import PasskeyConfig, b64url_decode
from fastplace.errors import ThrottleRequestsError, ValidationError
from fastplace.events import DomainEvent, listen
from fastplace.ratelimit import RateLimiter
from tests.auth.webauthn_fixture import SimulatedAuthenticator, b64url

RP_ID = "localhost"
ORIGIN = "http://localhost:9000"

USER_A = SimpleNamespace(
    id=7, name="Firoz", email="firoz@example.test", two_factor_confirmed_at=None
)
USER_B = SimpleNamespace(
    id=8, name="Other", email="other@example.test", two_factor_confirmed_at=None
)
TWO_FACTOR_USER = SimpleNamespace(
    id=9, name="Tofa", email="tofa@example.test", two_factor_confirmed_at=1700000000
)


def make_config(**overrides) -> PasskeyConfig:
    defaults = dict(
        rp_id=RP_ID,
        rp_name="Test",
        origins=[ORIGIN],
        timeout_ms=60000,
        user_verification="preferred",
        attestation="none",
        challenge_ttl=300,
        login_max_attempts=5,
    )
    defaults.update(overrides)
    return PasskeyConfig(**defaults)


def fake_request(session: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(session=session if session is not None else {}, ip="1.2.3.4")


def make_guard(**config_overrides) -> PasskeyGuard:
    return PasskeyGuard(store=PasskeyStore(), config=make_config(**config_overrides))


def make_authenticator(**overrides) -> SimulatedAuthenticator:
    return SimulatedAuthenticator(rp_id=RP_ID, origin=ORIGIN, **overrides)


def make_stranger_authenticator() -> SimulatedAuthenticator:
    """An authenticator holding a credential no user ever registered.

    assertion_response() requires a credential id to echo; a random one
    guarantees the guard's lookup misses.
    """
    import secrets

    stranger = make_authenticator()
    stranger.credential_id = secrets.token_bytes(32)
    return stranger


def rebind_credential_id(response: dict, credential_id: bytes) -> None:
    """Rewrite a registration payload to carry a different credential id.

    fmt="none" attestations have no signature, so patching the id inside
    authData (and the outer id/rawId) yields a fully valid payload for the
    SAME authenticator key — the deterministic way to reach the unique
    constraint with a duplicate id.
    """
    import cbor2

    attestation = cbor2.loads(b64url_decode(response["response"]["attestationObject"]))
    auth_data = bytearray(attestation["authData"])
    # layout: 32 rp_id_hash | 1 flags | 4 sign_count | 16 aaguid | 2 LCI | id | COSE key
    id_len = int.from_bytes(auth_data[53:55], "big")
    auth_data[55 : 55 + id_len] = credential_id
    attestation["authData"] = bytes(auth_data)
    response["id"] = response["rawId"] = b64url(credential_id)
    response["response"]["attestationObject"] = b64url(cbor2.dumps(dict(attestation)))


async def register_key(
    guard: PasskeyGuard,
    request: SimpleNamespace,
    user: SimpleNamespace,
    authenticator: SimulatedAuthenticator,
    name: str = "Test key",
) -> int:
    payload = await guard.registration_options(request, user)
    response = authenticator.registration_response(payload["challenge"])
    return await guard.register(request, user, name, response)


class EventRecorder:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    def __call__(self, event: DomainEvent) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [event.name for event in self.events]


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Isolated SQLite per case, same recipe as the passkey-store suite."""
    from fastplace.db import reset_db

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/passkey_guard.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    yield
    reset_db()


@pytest.fixture(autouse=True)
def _fresh_cache():
    """The lazy limiter lands on the process-wide memory cache — counters
    from one case must never leak into the next."""
    from fastplace.cache import reset_cache

    reset_cache()
    yield
    reset_cache()


@pytest.fixture(autouse=True)
def _provider_and_events(monkeypatch: pytest.MonkeyPatch):
    """Pin guard()'s provider to the dict singleton and isolate listeners."""
    from fastplace.events import reset_listeners

    monkeypatch.setattr(
        "fastplace.auth.guards.provider_from_config", lambda config_get: dict_provider
    )
    dict_provider.add(USER_A)
    dict_provider.add(USER_B)
    dict_provider.add(TWO_FACTOR_USER)
    reset_listeners()
    yield
    reset_listeners()


@pytest.fixture
def events() -> EventRecorder:
    recorder = EventRecorder()
    for name in ("passkey.registered", "passkey.deleted", "passkey.login", "passkey.confirm"):
        listen(name, recorder)
    return recorder


# -- manage -----------------------------------------------------------------


async def test_register_stores_credential_and_fires_event(events):
    guard = make_guard()
    request = fake_request()
    row_id = await register_key(guard, request, USER_A, make_authenticator(), "Chrome on Mac")

    assert row_id > 0
    listed = await guard.list_for(USER_A)
    assert len(listed) == 1
    assert listed[0]["name"] == "Chrome on Mac"
    registered = [e for e in events.events if e.name == "passkey.registered"]
    assert len(registered) == 1
    assert registered[0].payload["user_id"] == USER_A.id
    assert registered[0].payload["id"] == row_id


async def test_register_consumes_challenge():
    """A used challenge cannot register twice — single-use, fail closed."""
    guard = make_guard()
    request = fake_request()
    authenticator = make_authenticator()
    payload = await guard.registration_options(request, USER_A)
    response = authenticator.registration_response(payload["challenge"])

    await guard.register(request, USER_A, "First", response)
    with pytest.raises(ValidationError) as excinfo:
        await guard.register(request, USER_A, "Second", response)
    assert excinfo.value.errors == {"credential": ["Unable to verify this passkey."]}


async def test_register_duplicate_credential_id_is_422():
    guard = make_guard()
    request = fake_request()
    authenticator = make_authenticator()

    first_payload = await guard.registration_options(request, USER_A)
    first = authenticator.registration_response(first_payload["challenge"])
    await guard.register(request, USER_A, "First", first)
    first_id = authenticator.credential_id

    second_payload = await guard.registration_options(request, USER_A)
    duplicate = authenticator.registration_response(second_payload["challenge"])
    rebind_credential_id(duplicate, first_id)

    with pytest.raises(ValidationError) as excinfo:
        await guard.register(request, USER_A, "Second", duplicate)
    assert "already registered" in excinfo.value.errors["credential"][0]


async def test_register_integrity_error_maps_to_422_regardless_of_driver_text():
    """The unique race must stay a friendly 422 even when the driver text
    never says "unique" — MySQL reports "Duplicate entry ... for key"."""
    from sqlalchemy.exc import IntegrityError

    guard = make_guard()
    request = fake_request()

    async def raising_create(*args: object, **kwargs: object) -> int:
        raise IntegrityError(
            "Duplicate entry 'abc' for key 'webauthn_credentials.credential_id'",
            None,
            None,
        )

    guard._store.create = raising_create  # type: ignore[method-assign]

    payload = await guard.registration_options(request, USER_A)
    response = make_authenticator().registration_response(payload["challenge"])

    with pytest.raises(ValidationError) as excinfo:
        await guard.register(request, USER_A, "Racy", response)
    assert "already registered" in excinfo.value.errors["credential"][0]


async def test_delete_dispatches_event_and_returns_false_for_foreign_row(events):
    guard = make_guard()
    authenticator = make_authenticator()
    row_id = await register_key(guard, fake_request(), USER_A, authenticator)

    assert await guard.delete(fake_request(), USER_B, row_id) is False
    assert "passkey.deleted" not in events.names()

    assert await guard.delete(fake_request(), USER_A, row_id) is True
    assert "passkey.deleted" in events.names()
    assert await guard.list_for(USER_A) == []


# -- login ------------------------------------------------------------------


async def test_login_happy_path_logs_in(events):
    guard = make_guard()
    authenticator = make_authenticator()
    await register_key(guard, fake_request(), USER_A, authenticator)

    request = fake_request()  # fresh visitor session
    options = await guard.login_options(request)
    response = authenticator.assertion_response(options["challenge"])

    user = await guard.login(request, response)
    assert user is USER_A

    from fastplace.auth.guards import guard as session_guard

    resolved = await session_guard().user(request)
    assert resolved is not None and resolved.id == USER_A.id
    assert "passkey.login" in events.names()


async def test_login_unknown_credential_generic_failure():
    guard = make_guard()
    request = fake_request()
    payload = await guard.login_options(request)

    stranger = make_stranger_authenticator()
    response = stranger.assertion_response(payload["challenge"])

    with pytest.raises(ValidationError) as excinfo:
        await guard.login(request, response)
    assert excinfo.value.errors == {"credential": ["Unable to verify this passkey."]}
    assert "user_id" not in request.session


async def test_login_replayed_counter_rejected():
    guard = make_guard()
    authenticator = make_authenticator()
    await register_key(guard, fake_request(), USER_A, authenticator)

    first_request = fake_request()
    options = await guard.login_options(first_request)
    first = authenticator.assertion_response(options["challenge"])
    assert await guard.login(first_request, first) is USER_A

    # Same counter value, fresh challenge: the signature is valid but the
    # counter never advanced — a cloned authenticator fingerprint.
    authenticator.counter -= 1
    replay_request = fake_request()
    replay_options = await guard.login_options(replay_request)
    replay = authenticator.assertion_response(replay_options["challenge"])
    assert replay["id"] == first["id"]

    with pytest.raises(ValidationError) as excinfo:
        await guard.login(replay_request, replay)
    assert excinfo.value.errors == {"credential": ["Unable to verify this passkey."]}
    assert "user_id" not in replay_request.session


async def test_login_parks_two_factor_challenge_for_confirmed_users(events):
    guard = make_guard()
    authenticator = make_authenticator()
    await register_key(guard, fake_request(), TWO_FACTOR_USER, authenticator)

    request = fake_request()
    options = await guard.login_options(request)
    response = authenticator.assertion_response(options["challenge"])

    result = await guard.login(request, response)
    assert result is None
    from fastplace.auth.guards import TWO_FACTOR_CHALLENGE_KEY, TWO_FACTOR_REMEMBER_KEY

    assert request.session[TWO_FACTOR_CHALLENGE_KEY] == TWO_FACTOR_USER.id
    assert request.session[TWO_FACTOR_REMEMBER_KEY] is False
    assert "user_id" not in request.session  # no session login — 2FA still owed
    # Parked attempt forgiven — both throttle buckets cleared.
    narrow_key, ip_key = _login_keys(request, response)
    assert await RateLimiter().attempts(narrow_key) == 0
    assert await RateLimiter().attempts(ip_key) == 0
    assert "passkey.login" not in events.names()


async def test_login_throttles_after_max_attempts():
    guard = make_guard(login_max_attempts=2)
    stranger = make_stranger_authenticator()

    for _ in range(2):
        request = fake_request()
        payload = await guard.login_options(request)
        response = stranger.assertion_response(payload["challenge"])
        with pytest.raises(ValidationError):
            await guard.login(request, response)

    locked = fake_request()
    payload = await guard.login_options(locked)
    response = stranger.assertion_response(payload["challenge"])
    with pytest.raises(ThrottleRequestsError) as excinfo:
        await guard.login(locked, response)
    assert excinfo.value.retry_after >= 1


async def test_login_throttle_is_scoped_to_the_named_credential():
    """Repeated garbage aimed at one credential must not lock out other
    credentials at the same IP.

    Keying the lockout on the socket IP alone hands one attacker a switch
    that turns passkey login off for every user behind a shared egress IP
    (reverse proxy, carrier CGNAT): mint a guest session + CSRF token, POST
    a few garbage payloads, and the whole IP is locked for the decay
    window — repeatable forever. The bucket narrows to the presented
    credential id, so only credentials the attacker can already name lock.
    """
    guard = make_guard(login_max_attempts=2)
    stranger_a = make_stranger_authenticator()
    stranger_b = make_stranger_authenticator()
    assert stranger_a.credential_id != stranger_b.credential_id

    for _ in range(2):  # exhaust the bucket aimed at A's credential
        request = fake_request()
        payload = await guard.login_options(request)
        response = stranger_a.assertion_response(payload["challenge"])
        with pytest.raises(ValidationError):
            await guard.login(request, response)

    request = fake_request()  # same ip, a different named credential
    payload = await guard.login_options(request)
    response = stranger_b.assertion_response(payload["challenge"])
    with pytest.raises(ValidationError) as excinfo:
        await guard.login(request, response)
    assert excinfo.value.errors == {"credential": ["Unable to verify this passkey."]}


async def test_login_ip_backstop_locks_wide_scans():
    """The narrowed bucket is backed by a loose per-IP counter at a much
    higher ceiling: rotating garbage credential ids still locks out."""
    guard = make_guard(login_max_attempts=2)  # ip backstop = 2 * 10
    for _ in range(20):
        request = fake_request()
        payload = await guard.login_options(request)
        stranger = make_stranger_authenticator()  # fresh id every attempt
        response = stranger.assertion_response(payload["challenge"])
        with pytest.raises(ValidationError):
            await guard.login(request, response)

    request = fake_request()
    payload = await guard.login_options(request)
    stranger = make_stranger_authenticator()
    response = stranger.assertion_response(payload["challenge"])
    with pytest.raises(ThrottleRequestsError) as excinfo:
        await guard.login(request, response)
    assert excinfo.value.retry_after >= 1


# -- confirm ----------------------------------------------------------------


async def test_confirm_requires_uv():
    """Config 'preferred' must not weaken confirm — only UV=true passes."""
    guard = make_guard(user_verification="preferred")
    authenticator = make_authenticator()
    await register_key(guard, fake_request(), USER_A, authenticator)

    authenticator.user_verified = False  # presence only, no biometric/PIN
    request = fake_request()
    options = await guard.confirm_options(request, USER_A)
    response = authenticator.assertion_response(options["challenge"])

    with pytest.raises(ValidationError) as excinfo:
        await guard.confirm(request, USER_A, response)
    assert excinfo.value.errors == {"credential": ["Unable to verify this passkey."]}
    assert "password_confirmed_at" not in request.session


async def test_confirm_sets_password_stamp(events):
    guard = make_guard()
    authenticator = make_authenticator()
    await register_key(guard, fake_request(), USER_A, authenticator)

    request = fake_request()
    options = await guard.confirm_options(request, USER_A)
    response = authenticator.assertion_response(options["challenge"])

    assert await guard.confirm(request, USER_A, response) is True
    assert isinstance(request.session["password_confirmed_at"], int)
    assert "passkey.confirm" in events.names()

    row = await PasskeyStore().get_by_credential_id(b64url(authenticator.credential_id))
    assert row is not None
    assert row.sign_count == 1  # the confirm touched the counter
    assert row.last_used_at is not None


async def test_confirm_scoped_to_owner():
    """Another user's credential id is an unknown credential (IDOR-safe).

    The assertion is built from the challenge issued into USER_B's OWN
    session — the same session object confirm() consumes — so the ceremony
    verifies cleanly at the crypto layer. The only thing standing between B
    and A's credential is the guard's owner filter; deleting it makes this
    session stamp as B using A's key, which this test must catch.
    """
    guard = make_guard()
    authenticator = make_authenticator()
    await register_key(guard, fake_request(), USER_A, authenticator)

    request_b = fake_request()
    options_b = await guard.confirm_options(request_b, USER_B)  # B's (empty) allow list
    response = authenticator.assertion_response(options_b["challenge"])

    with pytest.raises(ValidationError) as excinfo:
        await guard.confirm(request_b, USER_B, response)
    assert excinfo.value.errors == {"credential": ["Unable to verify this passkey."]}
    assert "password_confirmed_at" not in request_b.session
