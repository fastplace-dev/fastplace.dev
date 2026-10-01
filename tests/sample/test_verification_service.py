"""VerificationService — signed links, mail, idempotent fulfillment (spec §4.11)."""

from __future__ import annotations

import re
import time
from types import SimpleNamespace

import pytest

from fastplace.auth.signing import sign
from fastplace.errors import AuthorizationError
from fastplace.events import DomainEvent, listen, reset_listeners
from fastplace.mail import clear_mail_outbox, mail_outbox

_URL_RE = re.compile(r"/email/verify/(\d+)/([0-9a-f]{64})\?expires=(\d+)")


@pytest.fixture(autouse=True)
async def users_db(monkeypatch, tmp_path):
    """Fresh sqlite + a User model registered on it (APP_KEY set so sign() works)."""
    from fastplace.db import reset_db

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/verify.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    monkeypatch.setenv("APP_KEY", "test-app-key-verification")
    monkeypatch.setenv("MAIL_DRIVER", "memory")
    reset_db()
    reset_listeners()
    clear_mail_outbox()
    from app.modules.accounts.models.user import User
    from fastplace.db import db

    await db.create_all()
    yield User
    reset_db()
    reset_listeners()
    clear_mail_outbox()


async def _make_user(User, email="verify@example.test"):
    return await Model_create(User, email)


async def Model_create(Model, email):
    return await Model.create(name="Verify", email=email, password_hash="x")


class TestSendLink:
    async def test_send_link_mails_the_signed_url(self, users_db):
        from app.modules.accounts.services.verification_service import VerificationService

        user = await _make_user(users_db)
        url = await VerificationService().send_link(user.id, user.email)
        assert url.startswith("http://localhost:9000/email/verify/")
        match = _URL_RE.search(url)
        assert match is not None
        assert match.group(1) == str(user.id)
        outbox = mail_outbox()
        assert outbox[-1].to == "verify@example.test"
        assert outbox[-1].subject == "Verify your email address"
        assert "/email/verify/" in outbox[-1].text


class TestFulfill:
    async def test_fulfill_marks_verified_and_dispatches_once(self, users_db):
        from app.modules.accounts.services.verification_service import VerificationService

        events: list[DomainEvent] = []
        listen("Verified", events.append)
        user = await _make_user(users_db)
        service = VerificationService()
        url = await service.send_link(user.id, user.email)
        match = _URL_RE.search(url)
        assert match is not None
        request = SimpleNamespace(user=user)
        await service.fulfill(request, match.group(1), match.group(2), match.group(3))
        fresh = await users_db.where(users_db.email == user.email).first()
        assert fresh.email_verified_at is not None
        # Replay of an already-verified click: idempotent, no second event.
        await service.fulfill(request, match.group(1), match.group(2), match.group(3))
        assert [event.name for event in events] == ["Verified"]

    async def test_tampered_signature_is_403(self, users_db):
        from app.modules.accounts.services.verification_service import VerificationService

        user = await _make_user(users_db)
        request = SimpleNamespace(user=user)
        with pytest.raises(AuthorizationError):
            await VerificationService().fulfill(
                request, str(user.id), "f" * 64, str(int(time.time()) + 3600)
            )

    async def test_expired_signature_is_403(self, users_db):
        from app.modules.accounts.services.verification_service import VerificationService

        user = await _make_user(users_db)
        signature, expires = sign(f"{user.id}|{user.email}", ttl=-10)
        request = SimpleNamespace(user=user)
        with pytest.raises(AuthorizationError):
            await VerificationService().fulfill(request, str(user.id), signature, str(expires))

    async def test_other_users_signature_is_403(self, users_db):
        from app.modules.accounts.services.verification_service import VerificationService

        user = await _make_user(users_db)
        signature, expires = sign("9999|miss@other.test")
        request = SimpleNamespace(user=user)
        with pytest.raises(AuthorizationError):
            await VerificationService().fulfill(request, str(user.id), signature, str(expires))

    async def test_non_int_expires_is_403(self, users_db):
        from app.modules.accounts.services.verification_service import VerificationService

        user = await _make_user(users_db)
        request = SimpleNamespace(user=user)
        with pytest.raises(AuthorizationError):
            await VerificationService().fulfill(request, str(user.id), "0" * 64, "abc")

    async def test_missing_request_user_is_403(self, users_db):
        from app.modules.accounts.services.verification_service import VerificationService

        with pytest.raises(AuthorizationError):
            await VerificationService().fulfill(SimpleNamespace(), "1", "0" * 64, "1")
