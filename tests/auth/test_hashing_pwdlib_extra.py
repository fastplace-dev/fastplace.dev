"""pwdlib-backed hasher contract — runs only where fastplace[pwdlib] is
installed (the default CI env is [dev]-only and skips these)."""

from __future__ import annotations

import pytest

pwdlib = pytest.importorskip("pwdlib")

from fastplace.auth.hashing import Hash  # noqa: E402


def test_fresh_argon2_digest_does_not_need_rehash():
    digest = Hash.make("secret")
    assert Hash.check("secret", digest)
    # Must answer False (this exact call crashed against real pwdlib:
    # PasswordHash has no needs_update attribute).
    assert Hash.needs_rehash(digest) is False


def test_scrypt_digest_needs_rehash_while_argon2_active():
    # The stdlib scrypt driver produced this digest; with pwdlib active
    # the login flow must flag it for upgrade (not crash, not False).
    scrypt_digest = Hash._default.hash("secret")
    assert scrypt_digest.startswith("$scrypt")
    assert Hash.needs_rehash(scrypt_digest) is True
