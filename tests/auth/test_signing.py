"""APP_KEY-signed values — HMAC-SHA256 over value|expires."""

from __future__ import annotations

import pytest

import fastplace.auth.signing as signing
from fastplace.auth.signing import sign, verify
from fastplace.errors import ConfigurationError


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    state = {"APP_KEY": "test-signing-key"}
    monkeypatch.setattr(signing, "config", lambda key, default=None: state.get(key, default))


def test_roundtrip():
    signature, expires = sign("7|a@b.test")
    assert verify("7|a@b.test", signature, str(expires))


def test_wrong_value_fails():
    signature, expires = sign("7|a@b.test")
    assert not verify("8|a@b.test", signature, str(expires))


def test_tampered_signature_fails():
    _, expires = sign("7|a@b.test")
    assert not verify("7|a@b.test", "0" * 64, str(expires))


def test_expired_value_fails():
    signature, expires = sign("7|a@b.test", ttl=-10)
    assert not verify("7|a@b.test", signature, str(expires))


def test_non_int_expires_fails():
    signature, expires = sign("7|a@b.test")
    assert not verify("7|a@b.test", signature, "abc")


def test_empty_app_key_refuses_to_sign(monkeypatch):
    monkeypatch.setattr(signing, "config", lambda key, default=None: None)
    with pytest.raises(ConfigurationError):
        sign("7|a@b.test")
