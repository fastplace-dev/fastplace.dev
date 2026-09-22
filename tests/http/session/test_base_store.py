"""SessionStore protocol + StoredSession payload (spec §4.1)."""

from __future__ import annotations

from fastplace.http.session.base import StoredSession, session_lifetime


def test_stored_session_carries_payload_and_last_activity():
    stored = StoredSession(payload={"_token": "abc"}, last_activity=100)
    assert stored.payload == {"_token": "abc"}
    assert stored.last_activity == 100


def test_session_lifetime_reads_config_default():
    # No SESSION_LIFETIME in the test env — the 7200 fallback applies.
    assert session_lifetime() == 7200
