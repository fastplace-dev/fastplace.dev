"""Shared Fastplace exception hierarchy.

Services raise domain exceptions from this hierarchy; the framework's HTTP
exception handlers translate them into responses for whichever edge made the
call (bridge page or unified API). Services never import from the HTTP layer.
"""

from __future__ import annotations


class FastplaceError(Exception):
    """Base class for every framework-raised error."""

    status_code: int = 400
    default_message: str = "Fastplace error"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class ConfigurationError(FastplaceError):
    """The application or framework configuration is invalid."""

    status_code = 500
    default_message = "Configuration error"


class ValidationError(FastplaceError):
    """Request or domain input validation failed."""

    status_code = 422
    default_message = "The given data was invalid."

    def __init__(self, message: str | None = None, errors: dict | None = None) -> None:
        super().__init__(message)
        self.errors = errors or {}


class NotFoundError(FastplaceError):
    """A requested resource does not exist."""

    status_code = 404
    default_message = "Resource not found."


class AuthenticationError(FastplaceError):
    """The request is not authenticated."""

    status_code = 401
    default_message = "Unauthenticated."


class AuthorizationError(FastplaceError):
    """The authenticated user may not perform this action."""

    status_code = 403
    default_message = "This action is unauthorized."


class ServerError(FastplaceError):
    """Unexpected framework or application failure."""

    status_code = 500
    default_message = "Server error."


class SearchCapabilityMissing(FastplaceError):
    """No vector- or full-text-capable backend is available for this search."""

    status_code = 500
    default_message = "No search-capable backend is configured."
