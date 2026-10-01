"""Ceremony-core tests — options shape, verification paths, challenge lifecycle."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse

from fastplace.auth.webauthn import (
    ChallengeStore,
    PasskeyConfig,
    assertion_options,
    ensure_webauthn,
    registration_options,
    verify_assertion,
    verify_registration,
)
from tests.auth.webauthn_fixture import SimulatedAuthenticator, b64url

RP_ID = "localhost"
ORIGIN = "http://localhost:9000"
USER = SimpleNamespace(id=7, name="Firoz", email="firoz@example.test")


def make_config(**overrides):
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


def make_authenticator() -> SimulatedAuthenticator:
    return SimulatedAuthenticator(rp_id=RP_ID, origin=ORIGIN)


def test_from_config_defaults_from_app_url(monkeypatch):
    # config() reads via fastplace.config — stub the AUTH_PASSKEYS/APP_URL
    # keys the implementation queries (env-var precedence is asserted too).
    import fastplace.config as config_module

    values = {
        "AUTH_PASSKEYS": {"enabled": True, "rp_id": None, "origins": None, "rp_name": None},
        "APP_URL": "http://localhost:9000",
        "APP_NAME": "Demo",
    }
    monkeypatch.setattr(config_module, "config", lambda key, default=None: values.get(key, default))
    config = PasskeyConfig.from_config()
    assert config.rp_id == "localhost"  # APP_URL host
    assert config.rp_name in ("Test", "Demo")  # falls back to APP_NAME
    assert config.origins == ["http://localhost:9000"]


def test_from_config_without_app_url_falls_back_to_default_port(monkeypatch):
    # Empty APP_URL: the origin is derived from the RP ID on the framework's
    # default port, keeping the ceremony origins aligned with `run dev`.
    import fastplace.config as config_module

    values = {
        "AUTH_PASSKEYS": {"enabled": True, "rp_id": None, "origins": None, "rp_name": None},
        "APP_URL": "",
        "APP_NAME": "Demo",
    }
    monkeypatch.setattr(config_module, "config", lambda key, default=None: values.get(key, default))
    config = PasskeyConfig.from_config()
    assert config.rp_id == "localhost"
    assert config.origins == ["http://localhost:9000"]


def test_env_overrides_block_values(monkeypatch):
    """Env vars win over the AUTH_PASSKEYS block — the config/auth.py rule."""
    import fastplace.config as config_module

    values = {
        "AUTH_PASSKEYS": {"enabled": True, "rp_id": "block.example", "rp_name": None},
        "APP_URL": "http://localhost:9000",
        "APP_NAME": "Demo",
        "APP_PASSKEYS_RP_ID": "env.example",
    }
    monkeypatch.setattr(config_module, "config", lambda key, default=None: values.get(key, default))
    assert PasskeyConfig.from_config().rp_id == "env.example"


def test_from_config_requires_enabled(monkeypatch):
    import fastplace.config as config_module

    monkeypatch.setattr(
        config_module,
        "config",
        lambda key, default=None: {"AUTH_PASSKEYS": {"enabled": False}}.get(key, default),
    )
    from fastplace.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="enabled"):
        PasskeyConfig.from_config()


def test_registration_options_shape_decodes_base64url():
    config = make_config()
    payload = registration_options(config, USER, exclude_ids=[])
    options = payload["options"]
    padded = options["challenge"] + "=" * (-len(options["challenge"]) % 4)
    challenge = base64.urlsafe_b64decode(padded)
    assert len(challenge) == 32
    assert (
        base64.urlsafe_b64decode(options["user"]["id"] + "=" * (-len(options["user"]["id"]) % 4))
        == b"7"
    )
    assert options["rp"]["id"] == RP_ID
    assert options["timeout"] == 60000


def test_registration_options_exclude_existing_credentials():
    config = make_config()
    existing = b64url(b"x" * 32)
    options = registration_options(config, USER, exclude_ids=[existing])["options"]
    assert options["excludeCredentials"][0]["id"] == existing


def test_verify_registration_happy_path():
    config = make_config()
    authenticator = make_authenticator()
    payload = registration_options(config, USER, exclude_ids=[])
    response = authenticator.registration_response(payload["challenge"])
    material = verify_registration(config, response, payload["challenge"])
    from tests.auth.webauthn_fixture import b64url as enc

    assert material.credential_id == enc(authenticator.credential_id)
    assert material.sign_count == 0
    assert material.user_verified is True
    assert material.transports == ["internal"]
    assert material.public_key  # non-empty b64url COSE key


def test_verify_registration_rejects_wrong_origin():
    config = make_config()
    authenticator = SimulatedAuthenticator(rp_id=RP_ID, origin="http://evil.test")
    payload = registration_options(config, USER, exclude_ids=[])
    response = authenticator.registration_response(payload["challenge"])
    with pytest.raises(InvalidRegistrationResponse):  # py-webauthn's own error
        verify_registration(config, response, payload["challenge"])


def test_verify_registration_replayed_challenge():
    """Replay is stopped by the ceremony layer, not the crypto layer.

    py-webauthn's verifier is deliberately stateless — determinism is a
    feature — so a raw re-verify of the same payload cannot fail. The
    single-use challenge is what makes a replayed ceremony impossible:
    the challenge store consumes on read, and the second consume is None.
    """
    config = make_config()
    authenticator = make_authenticator()
    payload = registration_options(config, USER, exclude_ids=[])
    response = authenticator.registration_response(payload["challenge"])
    verify_registration(config, response, payload["challenge"])  # first ceremony passes

    class Session(dict):
        pass

    store = ChallengeStore(Session(), ttl=config.challenge_ttl)
    store.issue("register", payload["challenge"])
    assert store.consume("register") == payload["challenge"]
    assert store.consume("register") is None  # replayed ceremony has no challenge


def test_assertion_happy_path_and_replay_of_low_counter():
    config = make_config()
    authenticator = make_authenticator()
    reg = registration_options(config, USER, exclude_ids=[])
    material = verify_registration(
        config, authenticator.registration_response(reg["challenge"]), reg["challenge"]
    )

    auth = assertion_options(config, allow_ids=[material.credential_id])
    options = auth["options"]
    assert options["allowCredentials"][0]["id"] == material.credential_id

    response = authenticator.assertion_response(auth["challenge"])
    verified = verify_assertion(
        config,
        response,
        auth["challenge"],
        stored_public_key=material.public_key,
        stored_sign_count=material.sign_count,
        require_uv=False,
    )
    assert verified.new_sign_count == 1
    assert verified.user_verified is True


def test_verify_assertion_bad_signature_fails():
    config = make_config()
    authenticator = make_authenticator()
    reg = registration_options(config, USER, exclude_ids=[])
    material = verify_registration(
        config, authenticator.registration_response(reg["challenge"]), reg["challenge"]
    )
    auth = assertion_options(config, allow_ids=[material.credential_id])
    response = authenticator.assertion_response(auth["challenge"])
    # Corrupt the signature — flip one bit.
    sig = bytearray(
        base64.urlsafe_b64decode(
            response["response"]["signature"] + "=" * (-len(response["response"]["signature"]) % 4)
        )
    )
    sig[0] ^= 0xFF
    response["response"]["signature"] = b64url(bytes(sig))
    with pytest.raises(InvalidAuthenticationResponse):
        verify_assertion(
            config,
            response,
            auth["challenge"],
            stored_public_key=material.public_key,
            stored_sign_count=0,
            require_uv=False,
        )


def test_challenge_store_single_use_and_namespaced():
    class FakeSession(dict):
        pass

    session = FakeSession()
    store = ChallengeStore(session, ttl=300)
    store.issue("register", "AAA")
    assert store.consume("register") == "AAA"
    assert store.consume("register") is None  # single-use

    store.issue("login", "BBB")
    assert store.consume("confirm") is None  # ceremony-namespaced
    assert store.consume("login") == "BBB"


def test_challenge_store_marks_server_session_dirty_on_issue_and_consume():
    """Nested-mutation visibility under the real ServerSession.

    ServerSession detects changes by shallow snapshot compare, so a nested
    dict mutated in place under its own key never flags dirty — the challenge
    would silently never persist. issue()/consume() must replace the nested
    store with a fresh copy so the session's dirty check sees the change.
    """
    from fastplace.http.session.middleware import ServerSession

    stored = ServerSession.hydrate(
        "sid-1", SimpleNamespace(payload={"user_id": 7}, last_activity=1)
    )
    assert not stored.is_dirty

    store = ChallengeStore(SimpleNamespace(session=stored), ttl=300)
    store.issue("register", "DIRTY-1")
    assert stored.is_dirty, "issue() must mark the session dirty or it never persists"

    # Simulate the persist cycle: payload written, session re-hydrated clean.
    written = dict(stored)
    reloaded = ServerSession.hydrate("sid-2", SimpleNamespace(payload=written, last_activity=1))
    assert (
        ChallengeStore(SimpleNamespace(session=reloaded), ttl=300).consume("register") == "DIRTY-1"
    )
    assert reloaded.is_dirty, "consume() must mark the session dirty or removal never persists"


def test_challenge_store_ttl_expiry(monkeypatch):
    import time as time_module

    import fastplace.auth.webauthn as core

    now = [1000.0]
    monkeypatch.setattr(time_module, "time", lambda: now[0])
    monkeypatch.setattr(core, "time", time_module)
    store = ChallengeStore({}, ttl=10)
    store.issue("login", "CCC")
    now[0] += 11
    assert store.consume("login") is None


def test_challenge_store_requires_session():
    # No session attribute -> issue/consume must not crash the endpoint:
    # consume returns None (fail closed).
    class NoSession:
        session = None

    assert ChallengeStore(NoSession(), ttl=300).consume("login") is None


def test_ensure_webauthn_message_names_the_extra(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "webauthn":
            raise ImportError("No module named 'webauthn'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(Exception, match="webauthn"):
        ensure_webauthn()
