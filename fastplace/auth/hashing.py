"""Password hashing — PHC-tagged digests with a zero-config stdlib default.

``Hash.make`` stores the algorithm and its parameters inside the digest
(``$scrypt$n=32768,r=8,p=1$<salt>$<hash>``), so verification never depends on
which settings produced it. The default driver is :class:`ScryptHasher`
(stdlib ``hashlib.scrypt`` — no dependency, OWASP-aligned parameters). When
the optional ``pwdlib[argon2]`` extra is installed, Argon2id becomes the
primary driver and existing scrypt digests keep verifying; login flows call
``Hash.needs_rehash`` to upgrade them in place.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from typing import Protocol

# scrypt work factors. n=2**15 with r=8, p=1 sits in the OWASP-recommended
# band while keeping local logins snappy; dklen=64 gives a wide compare.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64

_SALT_BYTES = 16

# OpenSSL caps scrypt at 32 MiB by default; n=2**15 * r=8 needs ~33 MiB.
_SCRYPT_MAXMEM = 64 * 1024 * 1024

_DIGEST_SHAPE = re.compile(
    r"^\$scrypt\$n=(?P<n>\d+),r=(?P<r>\d+),p=(?P<p>\d+)"
    r"\$(?P<salt>[A-Za-z0-9+/=]+)\$(?P<hash>[A-Za-z0-9+/=]+)$"
)


class PasswordHasher(Protocol):
    """A hashing driver — one algorithm family, tagged in the digest."""

    def hash(self, password: str) -> str: ...

    def verify(self, password: str, hashed: str) -> bool: ...

    def needs_update(self, hashed: str) -> bool: ...

    def claims(self, hashed: str) -> bool:
        """True when this driver owns ``hashed`` (tag check)."""
        ...


class ScryptHasher:
    """stdlib scrypt driver — the always-available default."""

    def __init__(self, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> None:
        self.n = n
        self.r = r
        self.p = p

    def hash(self, password: str) -> str:
        salt = secrets.token_bytes(_SALT_BYTES)
        digest = self._compute(password, salt, self.n, self.r, self.p, SCRYPT_DKLEN)
        return f"$scrypt$n={self.n},r={self.r},p={self.p}${_b64(salt)}${_b64(digest)}"

    def verify(self, password: str, hashed: str) -> bool:
        match = _DIGEST_SHAPE.match(hashed)
        if match is None:
            return False  # fail closed on anything we cannot parse
        salt = base64.b64decode(match["salt"])
        expected = base64.b64decode(match["hash"])
        digest = self._compute(
            password,
            salt,
            int(match["n"]),
            int(match["r"]),
            int(match["p"]),
            dklen=len(expected),
        )
        return hmac.compare_digest(digest, expected)

    def needs_update(self, hashed: str) -> bool:
        match = _DIGEST_SHAPE.match(hashed)
        if match is None:
            return True
        return (int(match["n"]), int(match["r"]), int(match["p"])) != (
            self.n,
            self.r,
            self.p,
        )

    def claims(self, hashed: str) -> bool:
        return hashed.startswith("$scrypt$")

    @staticmethod
    def _compute(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=dklen,
            maxmem=_SCRYPT_MAXMEM,
        )


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _build_extra_hasher() -> PasswordHasher | None:
    """Bind the pwdlib-backed Argon2id driver when the extra is installed."""
    try:
        from pwdlib import PasswordHash
        from pwdlib.hashers.argon2 import Argon2Hasher
    except ModuleNotFoundError:
        return None

    inner = PasswordHash((Argon2Hasher(),))

    class PwdlibHasher:
        def hash(self, password: str) -> str:
            return inner.hash(password)

        def verify(self, password: str, hashed: str) -> bool:
            return inner.verify(password, hashed)

        def needs_update(self, hashed: str) -> bool:
            return inner.needs_update(hashed)

        def claims(self, hashed: str) -> bool:
            return hashed.startswith(("$argon2", "$bcrypt"))

    return PwdlibHasher()


# None unless fastplace[pwdlib] / pwdlib[argon2] is installed in this process.
_extra_hasher: PasswordHasher | None = _build_extra_hasher()


class Hash:
    """Thin facade over the configured password hasher."""

    _default = ScryptHasher()

    @classmethod
    def make(cls, password: str) -> str:
        """Hash a plaintext password into a tagged, salted digest."""
        hasher = _extra_hasher if _extra_hasher is not None else cls._default
        return hasher.hash(password)

    @classmethod
    def check(cls, password: str, hashed: str) -> bool:
        """Verify a password against a stored digest (constant-time compare)."""
        if _extra_hasher is not None and _extra_hasher.claims(hashed):
            return _extra_hasher.verify(password, hashed)
        if hashed.startswith("$argon2"):
            raise RuntimeError(
                "Stored password digest requires pwdlib "
                "(pip install 'fastplace[pwdlib]' / 'pwdlib[argon2]'), "
                "which is not installed in this environment."
            )
        return cls._default.verify(password, hashed)

    @classmethod
    def needs_rehash(cls, hashed: str) -> bool:
        """True when the digest should be re-hashed with the current driver."""
        if _extra_hasher is not None:
            if _extra_hasher.claims(hashed):
                return _extra_hasher.needs_update(hashed)
            return True  # scrypt digest while argon2 is available → upgrade
        return cls._default.needs_update(hashed)
