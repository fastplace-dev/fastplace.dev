"""Two-factor primitives — TOTP secret, recovery codes, QR, verification.

Framework owns the cryptography-adjacent primitives (spec §4.13); the app
layer owns orchestration (enable/confirm/disable flows). The TOTP window is
±30s (``valid_window=1`` per the spec) and recovery codes use an
unambiguous alphabet so a hand-copied code survives fat fingers.
"""

from __future__ import annotations

import io
import secrets

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
    return bool(pyotp.TOTP(secret).verify(str(code or ""), valid_window=1))


def qr_code_svg(uri: str) -> str:
    """Inline SVG markup for ``uri`` — the setup modal injects it verbatim."""
    buffer = io.BytesIO()  # segno's writers emit bytes — decode to inline markup
    segno.make(uri, error="m").save(buffer, kind="svg", scale=8, border=4)
    return buffer.getvalue().decode("utf-8")
