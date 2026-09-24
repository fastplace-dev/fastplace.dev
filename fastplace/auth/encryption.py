"""Symmetric encryption at rest for sensitive auth columns (spec §4.13).

The two-factor secret and recovery codes are credential material: they are
never stored as plaintext. The key is derived from APP_KEY through
HKDF-SHA256 (domain-separated with a fixed salt+info), so rotating APP_KEY
invalidates old ciphertexts instead of silently decrypting them under a
different key. AESGCM gives authenticated encryption — tampering is
detected, not forgiven.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: Token prefix — future format changes bump this and old tokens fail fast.
_PREFIX = "fpaes1"
_HKDF_SALT = b"fastplace.auth.encryption.v1"
_HKDF_INFO = b"two_factor"
_KEY_LENGTH = 32  # AES-256


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _key(override: str | None = None) -> bytes:
    """Derive the AES key from explicit ``override`` material, else config APP_KEY."""
    from fastplace.config import config

    app_key = override if override is not None else str(config("APP_KEY", "") or "")
    if not app_key:
        from fastplace.errors import ConfigurationError

        raise ConfigurationError(
            "APP_KEY is required to encrypt or decrypt — set it in .env "
            "(python -c 'import secrets; print(secrets.token_urlsafe(48))') "
            "or pass an explicit key."
        )
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_LENGTH,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    ).derive(app_key.encode("utf-8"))


def encrypt(value: str, *, key: str | None = None) -> str:
    """Encrypt a UTF-8 string; returns ``fpaes1.<nonce>.<ciphertext+tag>``.

    ``key`` substitutes explicit key material for the config APP_KEY
    derivation (the CLI's ``--key`` option); it is never persisted.
    """
    import os

    nonce = os.urandom(12)  # GCM standard nonce size; fresh per token
    sealed = AESGCM(_key(key)).encrypt(nonce, value.encode("utf-8"), None)
    return f"{_PREFIX}.{_b64url_encode(nonce)}.{_b64url_encode(sealed)}"


def decrypt(token: str, *, key: str | None = None) -> str:
    """Verify + decrypt a token minted by :func:`encrypt`.

    ``key`` substitutes explicit key material for the config APP_KEY
    derivation, mirroring :func:`encrypt`.

    Raises ``ValueError`` for anything that is not an authentic token minted
    under the current key (tamper, truncation, foreign prefix, rotated key).
    """
    head, sep, rest = token.partition(".")
    if not sep or head != _PREFIX:
        raise ValueError("not an encrypted fastplace token")
    nonce_b64, sep, body_b64 = rest.partition(".")
    if not sep or not nonce_b64 or not body_b64:
        raise ValueError("encrypted token is truncated")
    # Outside the try — missing key material raises ConfigurationError, not ValueError.
    aes_key = _key(key)
    try:
        plaintext = AESGCM(aes_key).decrypt(
            _b64url_decode(nonce_b64), _b64url_decode(body_b64), None
        )
    except Exception as exc:  # InvalidTag, base64 junk — all "not authentic"
        raise ValueError("encrypted token failed authentication") from exc
    return plaintext.decode("utf-8")
