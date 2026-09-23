"""Authorization verdicts — what a gate check returns to its callers."""

from __future__ import annotations

from fastplace.errors import AuthorizationError


class Response:
    """An allow/deny verdict from a gate or policy check.

    ``bool(verdict)`` answers the check; ``as_error()`` maps a denial onto
    :class:`~fastplace.errors.AuthorizationError` for callers that raise.
    """

    __slots__ = ("allowed", "code", "message", "status_code")

    def __init__(
        self,
        allowed: bool,
        message: str | None = None,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        self.allowed = allowed
        self.message = message
        self.status_code = status_code
        self.code = code

    @classmethod
    def allow(cls) -> Response:
        return cls(True)

    @classmethod
    def deny(
        cls,
        message: str | None = None,
        status_code: int | None = None,
        code: str | None = None,
    ) -> Response:
        return cls(False, message=message, status_code=status_code, code=code)

    @classmethod
    def deny_as_not_found(cls) -> Response:
        # Mask the refusal as a 404 — to this caller the resource is not there.
        return cls(False, message="Resource not found.", status_code=404)

    def as_error(self) -> AuthorizationError | None:
        if self.allowed:
            return None
        return AuthorizationError(self.message, status_code=self.status_code, code=self.code)

    def __bool__(self) -> bool:
        return self.allowed
