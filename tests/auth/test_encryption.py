"""Encryption at rest — APP_KEY-derived AESGCM (spec §4.13 encrypted columns)."""

from __future__ import annotations

import pytest

from fastplace.auth.encryption import decrypt, encrypt
from fastplace.errors import ConfigurationError


@pytest.fixture(autouse=True)
def _app_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_KEY", "k" * 64)


class TestEncryptDecrypt:
    def test_round_trips_arbitrary_strings(self):
        token = encrypt("JBSWY3DPEHPK3PXP")
        assert token.startswith("fpaes1.")
        assert "JBSWY3DPEHPK3PXP" not in token  # ciphertext only, never plaintext
        assert decrypt(token) == "JBSWY3DPEHPK3PXP"

    def test_same_plaintext_mints_distinct_tokens(self):
        # Fresh nonce per encryption — equal plaintexts must not collide.
        assert encrypt("same") != encrypt("same")

    def test_round_trips_a_recovery_code_json_array(self):
        payload = '["ABCDE-FGHJK", "JKLMN-PQRST"]'
        assert decrypt(encrypt(payload)) == payload

    def test_tampered_ciphertext_raises_value_error(self):
        token = encrypt("secret")
        head, nonce, body = token.split(".")
        mangled = body[:-2] + ("AA" if not body.endswith("AA") else "BB")
        with pytest.raises(ValueError):
            decrypt(f"{head}.{nonce}.{mangled}")

    def test_truncated_token_raises_value_error(self):
        with pytest.raises(ValueError):
            decrypt("fpaes1.only-one-part")

    def test_foreign_prefix_raises_value_error(self):
        with pytest.raises(ValueError):
            decrypt("v2.AAAA.BBBB")

    def test_missing_app_key_refuses_to_encrypt(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", "")
        from fastplace.config import reset_config

        reset_config()
        with pytest.raises(ConfigurationError, match="APP_KEY"):
            encrypt("anything")
        reset_config()  # restore for other tests in this process

    def test_missing_app_key_refuses_to_decrypt(self, monkeypatch):
        token = encrypt("anything")
        monkeypatch.setenv("APP_KEY", "")
        from fastplace.config import reset_config

        reset_config()
        with pytest.raises(ConfigurationError, match="APP_KEY"):
            decrypt(token)
        reset_config()

    def test_key_rotation_breaks_old_tokens_loudly(self, monkeypatch):
        token = encrypt("under-the-old-key")
        monkeypatch.setenv("APP_KEY", "j" * 64)
        from fastplace.config import reset_config

        reset_config()
        with pytest.raises(ValueError):
            decrypt(token)
        reset_config()


class TestExplicitKeyOverride:
    """``key=`` substitutes explicit material for the config APP_KEY derivation."""

    def test_explicit_key_round_trips_without_app_key(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", "")
        from fastplace.config import reset_config

        reset_config()
        token = encrypt("env-blob", key="hand-carried")
        assert decrypt(token, key="hand-carried") == "env-blob"
        reset_config()

    def test_explicit_key_seals_against_the_config_app_key(self, monkeypatch):
        # A key= token must NOT open under APP_KEY — the override wins outright.
        token = encrypt("env-blob", key="hand-carried")
        with pytest.raises(ValueError):
            decrypt(token)

    def test_explicit_key_mismatch_raises_value_error(self):
        token = encrypt("env-blob", key="right")
        with pytest.raises(ValueError):
            decrypt(token, key="wrong")
