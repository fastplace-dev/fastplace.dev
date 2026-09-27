"""PasskeyStore tests — CRUD, owner filters, touch, humanized diffs."""

from __future__ import annotations

import time

import pytest

from fastplace.auth.passkeys import PasskeyStore, humanize_authenticator, humanize_diff
from fastplace.auth.webauthn import VerifiedMaterial


def material(credential_id: str = "AAA") -> VerifiedMaterial:
    return VerifiedMaterial(
        credential_id=credential_id,
        public_key="cHVia2V5",
        sign_count=0,
        backup_eligible=True,
        backup_state=False,
        transports=["internal"],
        aaguid=None,
        user_verified=True,
    )


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Isolated SQLite per case, same recipe as the remember-store suite."""
    from fastplace.db import reset_db

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/passkeys.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    yield
    reset_db()


@pytest.fixture
async def store():
    yield PasskeyStore()


async def test_create_and_list_roundtrip(store):
    user_id = 11
    await store.create(user_id, "Chrome on Mac", material())
    listed = await store.list_for(user_id)
    assert len(listed) == 1
    entry = listed[0]
    assert entry["name"] == "Chrome on Mac"
    assert set(entry) == {"id", "name", "authenticator", "created_at_diff", "last_used_at_diff"}
    assert entry["created_at_diff"] == "just now"
    assert entry["last_used_at_diff"] == "never"
    assert entry["authenticator"] == "This device"


async def test_get_by_credential_id_matches_browser_shape(store):
    """The stored id must equal the browser's unpadded b64url credential.id —
    a padded variant would make every login lookup miss (Review Focus #1)."""
    stored_id = "dGVzdC1jcmVkZW50aWFsLWlkLXdpdGgtbm8tcGFkZGluZw"
    await store.create(3, "Key", material(stored_id))
    row = await store.get_by_credential_id(stored_id)
    assert row is not None
    assert row.user_id == 3
    assert await store.get_by_credential_id(stored_id + "=") is None  # padding != id


async def test_delete_is_owner_filtered(store):
    row_id = await store.create(1, "Mine", material("MINE1"))
    assert await store.delete(2, row_id) is False  # not the owner (Review Focus #4)
    assert await store.get_by_credential_id("MINE1") is not None
    assert await store.delete(1, row_id) is True
    assert await store.get_by_credential_id("MINE1") is None


async def test_touch_updates_counter_backup_and_last_used(store):
    await store.create(5, "K", material("TOUCH1"))
    before = time.time()
    await store.touch("TOUCH1", sign_count=7, backup_state=True)
    row = await store.get_by_credential_id("TOUCH1")
    assert row.sign_count == 7
    assert row.backup_state is True
    assert row.last_used_at >= int(before) - 1


async def test_rows_for_is_scoped(store):
    await store.create(1, "a", material("R1"))
    await store.create(2, "b", material("R2"))
    assert [r.credential_id for r in await store.rows_for(1)] == ["R1"]


def test_humanize_diff_buckets():
    now = int(time.time())
    assert humanize_diff(None) == "never"
    assert humanize_diff(now) == "just now"
    assert humanize_diff(now - 5 * 60) == "5 minutes ago"
    assert humanize_diff(now - 2 * 3600) == "2 hours ago"
    assert humanize_diff(now - 3 * 86400) == "3 days ago"
    assert humanize_diff(now - 14 * 86400) == "2 weeks ago"
    assert humanize_diff(now - 60 * 86400) == "2 months ago"
    assert humanize_diff(now - 400 * 86400) == "1 years ago"


def test_humanize_authenticator_from_transports():
    assert humanize_authenticator(None, ["internal"]) == "This device"
    assert humanize_authenticator(None, ["usb", "nfc"]) == "Security key"
    assert humanize_authenticator(None, ["hybrid"]) == "Phone as a security key"
    assert humanize_authenticator(None, None) is None
    assert humanize_authenticator(None, ["ble"]) == "Portable authenticator"
