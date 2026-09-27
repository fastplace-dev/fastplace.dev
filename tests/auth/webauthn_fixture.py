"""ES256 WebAuthn simulator — a real-crypto fake authenticator.

Produces registration and assertion payloads the way a platform
authenticator would, so every verification test drives the real
py-webauthn codepath (no mocking of the verifier).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, field

import cbor2
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDSA,
    SECP256R1,
    EllipticCurvePrivateKey,
    derive_private_key,
)
from cryptography.hazmat.primitives.hashes import SHA256


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def rp_id_hash(rp_id: str) -> bytes:
    return hashlib.sha256(rp_id.encode()).digest()


FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_BE = 0x08
FLAG_BS = 0x10
FLAG_AT = 0x40  # attested credential data present (registration only)


def _cose_es256_key(public_key: object) -> bytes:
    """COSE key (RFC 8152): EC2/P-256/ES256 with raw x|y coordinates."""
    numbers = public_key.public_numbers()  # type: ignore[attr-defined]
    return cbor2.dumps(
        {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")}
    )


@dataclass
class SimulatedAuthenticator:
    rp_id: str
    origin: str
    aaguid: bytes = field(default_factory=lambda: bytes(16))
    counter: int = 0
    user_verified: bool = True
    backup_eligible: bool = False
    _key: EllipticCurvePrivateKey | None = None
    credential_id: bytes | None = None

    def _ensure_key(self) -> EllipticCurvePrivateKey:
        if self._key is None:
            self._key = derive_private_key(secrets.randbelow(2**256) | 1, SECP256R1())
        return self._key

    def _flags(self, attested: bool) -> int:
        flags = FLAG_UP | (FLAG_UV if self.user_verified else 0)
        if self.backup_eligible:
            flags |= FLAG_BE | FLAG_BS  # simulates a synced passkey
        if attested:
            flags |= FLAG_AT
        return flags

    def _client_data(self, type_: str, challenge: str) -> bytes:
        return json.dumps(
            {"type": type_, "challenge": challenge, "origin": self.origin},
            separators=(",", ":"),
        ).encode()

    def _sign(self, auth_data: bytes, client_data: bytes) -> bytes:
        message = auth_data + hashlib.sha256(client_data).digest()
        return self._ensure_key().sign(message, ECDSA(SHA256()))

    def registration_response(self, challenge: str) -> dict:
        key = self._ensure_key()
        self.credential_id = secrets.token_bytes(32)
        cose = _cose_es256_key(key.public_key())
        auth_data = (
            rp_id_hash(self.rp_id)
            + bytes([self._flags(attested=True)])
            + self.counter.to_bytes(4, "big")
            + self.aaguid
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + cose
        )
        attestation_object = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(self._client_data("webauthn.create", challenge)),
                "attestationObject": b64url(attestation_object),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }

    def assertion_response(self, challenge: str) -> dict:
        assert self.credential_id is not None, "register before asserting"
        self.counter += 1
        auth_data = (
            rp_id_hash(self.rp_id)
            + bytes([self._flags(attested=False)])
            + self.counter.to_bytes(4, "big")
        )
        client_data = self._client_data("webauthn.get", challenge)
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(self._sign(auth_data, client_data)),
            },
            "clientExtensionResults": {},
        }
