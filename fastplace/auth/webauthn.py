"""Passkey ceremony core — py-webauthn wrappers, base64url boundary, challenges.

Pure ceremony primitives: option building and response verification. No
storage, no I/O beyond config reads. Every base64url encode/decode of
WebAuthn binary fields happens here — callers never touch raw bytes.

py-webauthn is an optional dependency: everything routes through
:func:`ensure_webauthn`, so a missing extra fails with an actionable
install message instead of breaking application boot.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from fastplace.auth.providers import user_identifier
from fastplace.errors import ConfigurationError

CHALLENGES_KEY = "webauthn_challenges"

ZERO_AAGUID = "00000000-0000-0000-0000-000000000000"


def webauthn_available() -> bool:
    try:
        import webauthn  # noqa: F401
    except ImportError:
        return False
    return True


def ensure_webauthn() -> Any:
    """Import py-webauthn or fail with the actionable install message."""
    try:
        import webauthn
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise ConfigurationError(
            "Passkey support requires the 'webauthn' extra: pip install 'fastplace[webauthn]'"
        ) from exc
    return webauthn


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class PasskeyConfig:
    rp_id: str
    rp_name: str
    origins: list[str]
    timeout_ms: int
    user_verification: str
    attestation: str
    challenge_ttl: int
    login_max_attempts: int

    @classmethod
    def from_config(cls) -> PasskeyConfig:
        from fastplace.config import config

        block = dict(config("AUTH_PASSKEYS", default={}) or {})
        app_url = (config("APP_URL", default="") or "").rstrip("/")
        app_name = config("APP_NAME", default=None) or "Fastplace"

        def resolve(env_key: str, block_key: str, default: Any) -> Any:
            # Env vars always win over the config block.
            env = config(env_key, default=None)
            if env is not None and env != "":
                return env
            return block.get(block_key, default)

        enabled = resolve("APP_PASSKEYS_ENABLED", "enabled", False)
        if not enabled or str(enabled).lower() in ("0", "false", "no"):
            raise ConfigurationError(
                "AUTH_PASSKEYS.enabled is false — passkey ceremonies are not routed."
            )
        host = urlsplit(app_url).hostname if app_url else None
        rp_id = str(resolve("APP_PASSKEYS_RP_ID", "rp_id", None) or host or "localhost")
        origins_raw = resolve("APP_PASSKEYS_ORIGINS", "origins", None)
        if isinstance(origins_raw, str):
            origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
        elif isinstance(origins_raw, (list, tuple)):
            origins = [str(o) for o in origins_raw]
        else:
            origins = [app_url] if app_url else [f"http://{rp_id}:9000"]
        return cls(
            rp_id=rp_id,
            rp_name=str(resolve("APP_PASSKEYS_RP_NAME", "rp_name", None) or app_name),
            origins=origins,
            timeout_ms=int(resolve("APP_PASSKEYS_TIMEOUT_MS", "timeout_ms", 60000)),
            user_verification=str(
                resolve("APP_PASSKEYS_USER_VERIFICATION", "user_verification", "preferred")
            ),
            attestation=str(resolve("APP_PASSKEYS_ATTESTATION", "attestation", "none")),
            challenge_ttl=int(resolve("APP_PASSKEYS_CHALLENGE_TTL", "challenge_ttl", 300)),
            login_max_attempts=int(
                resolve("APP_PASSKEYS_LOGIN_MAX_ATTEMPTS", "login_max_attempts", 5)
            ),
        )


@dataclass(frozen=True)
class VerifiedMaterial:
    credential_id: str
    public_key: str
    sign_count: int
    backup_eligible: bool
    backup_state: bool
    transports: list[str]
    aaguid: str | None
    user_verified: bool


@dataclass(frozen=True)
class VerifiedAssertion:
    new_sign_count: int
    user_verified: bool
    backup_eligible: bool
    backup_state: bool


def _webauthn_structs() -> Any:
    """Structs are imported lazily with the library — the extra is optional."""
    from webauthn.helpers import structs

    return structs


def _rp_descriptor(credential_id_b64url: str) -> Any:
    structs = _webauthn_structs()
    return structs.PublicKeyCredentialDescriptor(id=b64url_decode(credential_id_b64url))


def _issue(lib: Any, options: Any) -> dict[str, Any]:
    """Serialize options and pull the challenge back out of the JSON itself,
    so the stored challenge is exactly what the client received."""
    payload = json.loads(lib.options_to_json(options))
    return {"options": payload, "challenge": payload["challenge"]}


def registration_options(
    config: PasskeyConfig, user: Any, exclude_ids: list[str]
) -> dict[str, Any]:
    lib = ensure_webauthn()
    structs = _webauthn_structs()
    identifier = user_identifier(user)
    email = getattr(user, "email", None) or str(identifier)
    name = getattr(user, "name", None) or email
    challenge = os.urandom(32)
    options = lib.generate_registration_options(
        rp_id=config.rp_id,
        rp_name=config.rp_name,
        user_name=email,
        user_id=str(identifier).encode("utf-8")[:64],
        user_display_name=name,
        challenge=challenge,
        timeout=config.timeout_ms,
        attestation=structs.AttestationConveyancePreference(config.attestation),
        authenticator_selection=structs.AuthenticatorSelectionCriteria(
            resident_key=structs.ResidentKeyRequirement.PREFERRED,
            user_verification=structs.UserVerificationRequirement(config.user_verification),
        ),
        exclude_credentials=[_rp_descriptor(cid) for cid in exclude_ids],
    )
    return _issue(lib, options)


def verify_registration(
    config: PasskeyConfig, credential: dict, expected_challenge: str
) -> VerifiedMaterial:
    lib = ensure_webauthn()
    result = lib.verify_registration_response(
        credential=credential,
        expected_challenge=b64url_decode(expected_challenge),
        expected_rp_id=config.rp_id,
        expected_origin=config.origins,
        require_user_presence=True,
        require_user_verification=config.user_verification == "required",
    )
    response = credential.get("response") or {}
    transports = response.get("transports") or []
    aaguid = getattr(result, "aaguid", None)
    # py-webauthn's VerifiedRegistration carries no readable BE/BS pair across
    # versions — read the flags straight out of the payload's authData.
    backup_eligible, backup_state = _flags_booleans(credential)
    return VerifiedMaterial(
        credential_id=b64url_encode(result.credential_id),
        public_key=b64url_encode(result.credential_public_key),
        sign_count=int(result.sign_count),
        backup_eligible=backup_eligible,
        backup_state=backup_state,
        transports=[str(t) for t in transports],
        aaguid=None if not aaguid or aaguid == ZERO_AAGUID else str(aaguid),
        user_verified=bool(result.user_verified),
    )


def assertion_options(
    config: PasskeyConfig,
    allow_ids: list[str],
    user_verification: str | None = None,
) -> dict[str, Any]:
    lib = ensure_webauthn()
    structs = _webauthn_structs()
    options = lib.generate_authentication_options(
        rp_id=config.rp_id,
        timeout=config.timeout_ms,
        allow_credentials=[_rp_descriptor(cid) for cid in allow_ids],
        user_verification=structs.UserVerificationRequirement(
            user_verification or config.user_verification
        ),
    )
    return _issue(lib, options)


def verify_assertion(
    config: PasskeyConfig,
    credential: dict,
    expected_challenge: str,
    stored_public_key: str,
    stored_sign_count: int,
    require_uv: bool = False,
) -> VerifiedAssertion:
    lib = ensure_webauthn()
    result = lib.verify_authentication_response(
        credential=credential,
        expected_challenge=b64url_decode(expected_challenge),
        expected_rp_id=config.rp_id,
        expected_origin=config.origins,
        credential_public_key=b64url_decode(stored_public_key),
        credential_current_sign_count=int(stored_sign_count),
        require_user_verification=require_uv,
    )
    backup_eligible, backup_state = _flags_booleans(credential)
    return VerifiedAssertion(
        new_sign_count=int(result.new_sign_count),
        user_verified=bool(result.user_verified),
        backup_eligible=backup_eligible,
        backup_state=backup_state,
    )


def _authdata_flags(credential: dict) -> int:
    response = credential.get("response") or {}
    auth_data = response.get("authenticatorData")
    if not auth_data:
        # Registration payloads carry flags inside attestationObject.authData.
        attestation = response.get("attestationObject")
        if not attestation:
            return 0
        import cbor2

        auth_data = cbor2.loads(b64url_decode(attestation)).get("authData", b"")
    # Assertions carry base64url; registration's decoded CBOR holds raw bytes.
    raw = auth_data if isinstance(auth_data, bytes) else b64url_decode(auth_data)
    if len(raw) < 6:
        return 0
    return struct.unpack("B", raw[32:33])[0]


def _flags_booleans(credential: dict) -> tuple[bool, bool]:
    flags = _authdata_flags(credential)
    return bool(flags & 0x08), bool(flags & 0x10)  # BE, BS


class ChallengeStore:
    """Session-backed ceremony challenges: single-use, TTL, namespaced.

    Challenges live under one session key, namespaced by ceremony; a
    challenge issued for login cannot verify a confirm or registration.
    Reading a challenge consumes it. No session, no ceremony.
    """

    def __init__(self, request: Any, ttl: int = 300) -> None:
        # A request exposes the session dict as `.session`; a bare dict is
        # accepted directly as the session (tests, callers without a request).
        session = request if isinstance(request, dict) else getattr(request, "session", None)
        self._session: Any = session if isinstance(session, dict) else None
        self._ttl = ttl

    def _store(self) -> dict[str, dict[str, Any]]:
        if self._session is None:
            return {}
        raw = self._session.get(CHALLENGES_KEY)
        return raw if isinstance(raw, dict) else {}

    def issue(self, ceremony: str, challenge: str) -> None:
        if self._session is None:
            return
        store = dict(self._store())
        store[ceremony] = {"challenge": challenge, "expires_at": time.time() + self._ttl}
        # Re-assign a fresh copy, never the mutated object itself: the
        # session's dirty tracking is a shallow snapshot compare, so writing
        # back the same nested dict it already holds looks like no change and
        # the challenge silently never persists.
        self._session[CHALLENGES_KEY] = dict(store)

    def consume(self, ceremony: str) -> str | None:
        # Pop from a copy: the loaded snapshot still aliases the live nested
        # dict, so popping in place would empty both and hide the removal.
        store = dict(self._store())
        entry = store.get(ceremony)
        if self._session is not None:
            store.pop(ceremony, None)
            # Same copy-on-write rule as issue(): the removal must be visible
            # to the dirty check or the consumed challenge can resurrect.
            self._session[CHALLENGES_KEY] = store
        if not entry or entry.get("expires_at", 0) < time.time():
            return None
        challenge = entry.get("challenge")
        return str(challenge) if challenge else None
