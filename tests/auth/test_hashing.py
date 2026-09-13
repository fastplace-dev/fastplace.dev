"""T4.1 — ``Hash.make`` / ``Hash.check`` / ``Hash.needs_rehash``.

Hash digests are PHC-tagged: the stored string names its own algorithm and
parameters so verification never depends on which driver produced it. The
default driver is stdlib scrypt (zero-config); when the optional
``pwdlib[argon2]`` extra is installed it becomes the primary driver and
``needs_rehash`` flags older scrypt digests for upgrade-on-login.
"""

from __future__ import annotations

import base64
import hashlib
import re

import pytest

from fastplace.auth import hashing
from fastplace.auth.hashing import Hash, ScryptHasher

SCRYPT_SHAPE = re.compile(
    r"^\$scrypt\$n=(\d+),r=(\d+),p=(\d+)\$([A-Za-z0-9+/=]+)\$([A-Za-z0-9+/=]+)$"
)


class FakeArgon2Hasher:
    """Stand-in for the pwdlib-backed driver, exercised via monkeypatch."""

    def hash(self, password: str) -> str:
        return f"$argon2id$v=19$fake${password}"

    def verify(self, password: str, hashed: str) -> bool:
        return hashed == f"$argon2id$v=19$fake${password}"

    def needs_update(self, hashed: str) -> bool:
        return False

    def claims(self, hashed: str) -> bool:
        return hashed.startswith("$argon2")


class TestHashMake:
    def test_produces_a_tagged_scrypt_digest_by_default(self):
        hashed = Hash.make("s3cret!")
        assert SCRYPT_SHAPE.match(hashed), hashed

    def test_salts_are_random_per_call(self):
        self_a = Hash.make("same-password")
        self_b = Hash.make("same-password")
        assert self_a != self_b

    def test_scrypt_parameters_are_recorded_in_the_digest(self):
        hashed = Hash.make("pw")
        match = SCRYPT_SHAPE.match(hashed)
        assert match is not None
        n, r, p, _, _ = match.groups()
        assert (int(n), int(r), int(p)) == (hashing.SCRYPT_N, hashing.SCRYPT_R, hashing.SCRYPT_P)


class TestHashCheck:
    def test_verifies_the_correct_password(self):
        assert Hash.check("s3cret!", Hash.make("s3cret!")) is True

    def test_rejects_a_wrong_password(self):
        assert Hash.check("wrong", Hash.make("s3cret!")) is False

    @pytest.mark.parametrize(
        "stored",
        ["", "plaintext", "$scrypt$garbage", "$bcrypt$2a$07$notreally"],
    )
    def test_fails_closed_on_malformed_or_unknown_digests(self, stored):
        assert Hash.check("anything", stored) is False

    def test_verifies_digests_hashed_with_non_default_parameters(self):
        # Parameter flexibility: a digest created with a weaker n=2**14 must
        # still verify — parameters live inside the stored string.
        legacy = ScryptHasher(n=2**14)
        hashed = legacy.hash("legacy-password")
        assert hashed != Hash.make("legacy-password")
        assert Hash.check("legacy-password", hashed) is True

    def test_digest_round_trips_against_a_direct_scrypt_recomputation(self):
        hashed = Hash.make("deterministic-check")
        match = SCRYPT_SHAPE.match(hashed)
        assert match is not None
        n, r, p, salt_b64, hash_b64 = match.groups()
        digest = hashlib.scrypt(
            b"deterministic-check",
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(base64.b64decode(hash_b64)),
            maxmem=64 * 1024 * 1024,  # match the hasher's OpenSSL budget
        )
        assert base64.b64encode(digest).decode() == hash_b64


class TestHashNeedsRehash:
    def test_fresh_default_digest_needs_no_rehash(self):
        assert Hash.needs_rehash(Hash.make("pw")) is False

    def test_digest_with_weaker_parameters_needs_rehash(self):
        legacy = ScryptHasher(n=2**14).hash("pw")
        assert Hash.needs_rehash(legacy) is True

    def test_unknown_format_needs_rehash(self):
        assert Hash.needs_rehash("not-a-hash") is True


class TestArgon2UpgradePath:
    """pwdlib-backed driver selected at runtime when the extra is installed."""

    def test_make_uses_the_extra_hasher_when_available(self, monkeypatch):
        fake = FakeArgon2Hasher()
        monkeypatch.setattr(hashing, "_extra_hasher", fake)
        assert Hash.make("pw") == "$argon2id$v=19$fake$pw"

    def test_check_dispatches_argon2_tags_to_the_extra_hasher(self, monkeypatch):
        fake = FakeArgon2Hasher()
        monkeypatch.setattr(hashing, "_extra_hasher", fake)
        assert Hash.check("pw", "$argon2id$v=19$fake$pw") is True
        assert Hash.check("nope", "$argon2id$v=19$fake$pw") is False

    def test_check_raises_when_an_argon2_digest_has_no_pwdlib(self, monkeypatch):
        monkeypatch.setattr(hashing, "_extra_hasher", None)
        with pytest.raises(RuntimeError, match="pwdlib"):
            Hash.check("pw", "$argon2id$v=19$fake$pw")

    def test_scrypt_digests_flag_for_rehash_when_argon2_is_available(self, monkeypatch):
        hashed = Hash.make("pw")  # scrypt digest, produced pre-upgrade
        monkeypatch.setattr(hashing, "_extra_hasher", FakeArgon2Hasher())
        assert Hash.needs_rehash(hashed) is True
