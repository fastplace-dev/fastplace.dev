"""TOTP engine — secret/codes/URI/verify/QR (spec §4.13)."""

from __future__ import annotations

import re

import pyotp

from fastplace.auth.two_factor import (
    RECOVERY_CODE_COUNT,
    generate_recovery_codes,
    generate_secret,
    otp_auth_uri,
    qr_code_svg,
    verify_code,
)

CODE_RE = re.compile(r"^[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{5}-[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{5}$")


class TestSecret:
    def test_secret_is_32_char_base32(self):
        secret = generate_secret()
        assert len(secret) == 32
        assert re.fullmatch(r"[A-Z2-7]{32}", secret)

    def test_secrets_are_unique(self):
        assert generate_secret() != generate_secret()


class TestRecoveryCodes:
    def test_generates_ten_codes_in_the_unguessable_format(self):
        codes = generate_recovery_codes()
        assert len(codes) == RECOVERY_CODE_COUNT == 10
        for code in codes:
            assert CODE_RE.fullmatch(code), code

    def test_codes_are_unique_within_a_batch(self):
        assert len(set(generate_recovery_codes())) == RECOVERY_CODE_COUNT

    def test_batches_differ(self):
        assert set(generate_recovery_codes()) != set(generate_recovery_codes())


class TestOtpAuthUri:
    def test_uri_targets_the_account_with_app_issuer(self):
        secret = generate_secret()
        uri = otp_auth_uri(secret, "firoz@example.test")
        assert uri.startswith("otpauth://totp/")
        assert "firoz%40example.test" in uri
        assert f"secret={secret}" in uri


class TestVerifyCode:
    def test_accepts_the_current_totp_code(self):
        secret = generate_secret()
        assert verify_code(secret, pyotp.TOTP(secret).now()) is True

    def test_accepts_the_previous_window_code(self):
        # ±30s (valid_window=1) — a code from 30s ago still verifies.
        import time

        secret = generate_secret()
        previous = pyotp.TOTP(secret).at(int(time.time()) - 30)
        assert verify_code(secret, previous) is True

    def test_rejects_a_code_outside_the_window(self):
        import time

        secret = generate_secret()
        stale = pyotp.TOTP(secret).at(int(time.time()) - 120)
        assert verify_code(secret, stale) is False

    def test_rejects_garbage(self):
        secret = generate_secret()
        assert verify_code(secret, "000000") is False
        assert verify_code(secret, "") is False
        assert verify_code(secret, "not-a-code") is False

    def test_rejects_a_code_for_a_different_secret(self):
        assert verify_code(generate_secret(), pyotp.TOTP(generate_secret()).now()) is False


class TestQrCodeSvg:
    def test_returns_inline_svg_markup(self):
        svg = qr_code_svg("otpauth://totp/Fastplace:firoz?secret=JBSWY3DPEHPK3PXP")
        assert svg.lstrip().startswith("<?xml")
        assert "<svg" in svg
        assert "</svg>" in svg
        assert "otpauth" not in svg  # the URI is encoded as modules, not text

    def test_different_uris_render_different_svgs(self):
        a = qr_code_svg("otpauth://totp/Fastplace:a?secret=JBSWY3DPEHPK3PXP")
        b = qr_code_svg("otpauth://totp/Fastplace:b?secret=MFRGGZDFMZTWQ2LK")
        assert a != b
