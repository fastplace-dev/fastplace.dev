"""Company-context middleware — bind each request to one company.

Register after ``ResolveUserMiddleware`` (the default resolution reads
``request.user``) and before ``CsrfMiddleware``::

    MIDDLEWARE = [
        "app.http.middleware.resolve_user.ResolveUserMiddleware",
        "fastplace_tenancy.middleware.CompanyContextMiddleware",
        "app.http.middleware.csrf.CsrfMiddleware",
    ]

Resolution order (overridable via ``_resolve_company_id``):

1. ``request.session["company_id"]`` — an explicit company switch,
2. the authenticated user's first membership,
3. nothing — the context stays unbound and company-scoped queries fail
   closed rather than assuming a tenant.
"""

from __future__ import annotations

from typing import Any

from fastplace.http.middleware import Middleware
from fastplace.http.request import Request
from fastplace.http.response import Response
from fastplace_tenancy.context import company_context

#: ASGI-scope key — shared per request like ``fastplace_user``, so every
#: Request wrapper (middleware's and controller's) sees the same binding.
SCOPE_COMPANY_KEY = "fastplace_company_id"


def request_company_id(request: Request) -> Any | None:
    """The company this request is bound to (``None`` when unbound)."""
    return request.scope.get(SCOPE_COMPANY_KEY)


class CompanyContextMiddleware(Middleware):
    """Bind the active company for the whole request (contextvar + scope)."""

    def __init__(self, *, session_key: str = "company_id") -> None:
        self.session_key = session_key

    async def handle(self, request: Request, call_next) -> Response:
        company_id = await self._resolve_company_id(request)
        if company_id is None:
            # Anonymous / unbound request: leave the context unset — tenant
            # queries raise MissingCompanyContext instead of guessing.
            return await call_next(request)
        request.scope[SCOPE_COMPANY_KEY] = company_id
        async with company_context(company_id):
            return await call_next(request)

    async def _resolve_company_id(self, request: Request) -> Any | None:
        """Session switch first (validated!), then the user's membership.

        The session value is a *request*, not a grant: it binds only when the
        authenticated user actually holds a membership in that company —
        otherwise the value is ignored and the default membership applies.
        App switch endpoints should pair the write with
        :func:`~fastplace_tenancy.require_membership`; this check is the
        backstop when they do not.

        Override to source the company differently (subdomain, header, JWT
        claim) — return the company id, or ``None`` to leave the request
        unbound.
        """
        user = request.user
        user_id = getattr(user, "id", None) if user is not None else None
        from_session = request.session.get(self.session_key)
        from fastplace_tenancy.models import CompanyMembership

        if from_session is not None and user_id is not None:
            membership = await CompanyMembership.where(
                CompanyMembership.user_id == user_id,
                CompanyMembership.company_id == from_session,
            ).first()
            if membership is not None:
                return from_session
            # Not a member — the switch is ignored, not honored.
        if user_id is None:
            return None
        # Deterministic default: the earliest membership, not whatever the
        # planner returns first.
        membership = (
            await CompanyMembership.where(CompanyMembership.user_id == user_id)
            .order_by(CompanyMembership.id)  # type: ignore[attr-defined]
            .first()
        )
        return membership.company_id if membership else None
