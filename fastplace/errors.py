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


class ThrottleRequestsError(FastplaceError):
    """Too many attempts — the client must wait (Retry-After in seconds)."""

    status_code = 429
    default_message = "Too many attempts. Try again later."

    def __init__(self, message: str | None = None, *, retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = max(0, int(retry_after))


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


class AiError(FastplaceError):
    """The AI engine failed — round limits, provider misuse, bad assembly."""

    status_code = 500
    default_message = "AI engine error."


class CacheSerializationError(FastplaceError):
    """A cache value cannot be serialized by the configured driver.

    Raised at ``put()`` time (redis stores JSON) — failing before the write
    beats a value that silently never lands in the cache.
    """

    status_code = 500
    default_message = "The cache value cannot be serialized."


class SearchCapabilityMissing(FastplaceError):
    """No vector- or full-text-capable backend is available for this search."""

    status_code = 500
    default_message = "No search-capable backend is configured."


class MassAssignmentError(FastplaceError):
    """A mass-assignment payload tried to set a guarded attribute.

    Guarded by default: the primary key and the audit/tombstone columns
    (``created_at`` / ``updated_at`` / ``deleted_at``). Models may narrow or
    widen the surface with ``__fillable__`` / ``__guarded__`` (OWASP mass
    assignment). Direct attribute assignment remains the escape hatch.
    """

    status_code = 422
    default_message = "The attribute is not mass-assignable."
