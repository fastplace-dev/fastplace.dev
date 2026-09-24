"""Two-factor primitives — TOTP secret, recovery codes, QR, verification.

Framework owns the cryptography-adjacent primitives (spec §4.13); the app
layer owns orchestration (enable/confirm/disable flows). The TOTP window is
±30s (``valid_window=1`` per the spec) and recovery codes use an
unambiguous alphabet so a hand-copied code survives fat fingers.
"""

from __future__ import annotations

import datetime
import io
import secrets
import time

import pyotp
import segno

#: How many one-time recovery codes a setup mints (spec §4.13: 10).
RECOVERY_CODE_COUNT = 10

#: No 0/O/1/I/L — every remaining character is visually unambiguous.
_RECOVERY_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def generate_secret() -> str:
    """A fresh TOTP shared secret (32-char base32, authenticator-standard)."""
    return pyotp.random_base32()


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """``count`` single-use codes, each ``XXXXX-XXXXX`` (~50 bits of entropy)."""
    return [
        "-".join("".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(5)) for _ in range(2))
        for _ in range(count)
    ]


def otp_auth_uri(secret: str, account_name: str) -> str:
    """The otpauth:// URI authenticator apps ingest (issuer = APP_NAME)."""
    from fastplace.config import config

    issuer = str(config("APP_NAME", "Fastplace") or "Fastplace")
    return pyotp.totp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)


def verify_code(secret: str, code: str) -> bool:
    """True when ``code`` is a current-window TOTP for ``secret`` (±30s)."""
    return verify_code_step(secret, code) is not None


def verify_code_step(secret: str, code: str, *, now: int | None = None) -> int | None:
    """The timestep ``code`` matched, or None when it does not verify.

    Single-use bookkeeping needs WHICH window accepted the code, not just
    that one did: callers persist the step as a high-water mark and refuse
    steps at or below it on the next challenge. Candidates resolve oldest
    first, so a code valid across a step boundary is charged to the earlier
    step — the mark can never move backwards. ``now`` (unix seconds) is
    injectable for deterministic tests.
    """
    totp = pyotp.TOTP(secret)
    code = str(code or "")
    moment = now if now is not None else int(time.time())
    for candidate in (moment - 30, moment, moment + 30):
        # pyotp accepts a unix timestamp at runtime but types for_time as
        # datetime — hand it the epoch as an aware UTC datetime.
        at = datetime.datetime.fromtimestamp(candidate, tz=datetime.UTC)
        if totp.verify(code, for_time=at):
            return candidate // 30
    return None


def qr_code_svg(uri: str) -> str:
    """Inline SVG markup for ``uri`` — the setup modal injects it verbatim."""
    buffer = io.BytesIO()  # segno's writers emit bytes — decode to inline markup
    segno.make(uri, error="m").save(buffer, kind="svg", scale=8, border=4)
    return buffer.getvalue().decode("utf-8")
