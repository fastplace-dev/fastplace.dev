"""Users table gains the §5 two-factor columns; secrets never serialize."""

from __future__ import annotations

import datetime


class TestUserTwoFactorColumns:
    def test_model_declares_the_three_columns(self):
        # Imported inside the test (mirroring every other sample-app model
        # test): the autouse fixture purges app.* modules + mappers, so a
        # module-level import would bind a stale, unmapped class.
        from app.modules.accounts.models.user import User

        columns = User.__table__.columns
        assert "two_factor_secret" in columns
        assert "two_factor_recovery_codes" in columns
        assert "two_factor_confirmed_at" in columns
        assert all(
            columns[name].nullable
            for name in (
                "two_factor_secret",
                "two_factor_recovery_codes",
                "two_factor_confirmed_at",
            )
        )

    def test_secret_columns_stay_hidden_from_serialization(self):
        from app.modules.accounts.models.user import User

        user = User(
            name="Firoz",
            email="two-factor@example.test",
            password_hash="hash",
            two_factor_secret="fpaes1.AAAA.BBBB",
            two_factor_recovery_codes='["ABCDE-FGHJK"]',
            two_factor_confirmed_at=datetime.datetime(2026, 9, 23, tzinfo=datetime.UTC),
        )
        dumped = user.to_dict()
        assert "two_factor_secret" not in dumped
        assert "two_factor_recovery_codes" not in dumped
        assert "password_hash" not in dumped
        # The timestamp is not credential material — it may serialize.
        assert dumped.get("two_factor_confirmed_at") is not None

    def test_fillable_still_excludes_two_factor_columns(self):
        # Mass assignment can never plant ciphertext columns; services set
        # them as explicit attribute assignments.
        from app.modules.accounts.models.user import User

        assert User.__fillable__ == {"name", "email", "password_hash", "email_verified_at"}
