"""Membership authorization — the service-layer tenant check.

Services call :func:`require_membership` before touching company data; the
errors are :class:`FastplaceError` subclasses, so the kernel answers with
403 JSON instead of a traceback.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from fastplace_tenancy.context import (
    CompanyRoleRequired,
    MissingCompanyContext,
    NotCompanyMember,
    require_company_context,
)


async def require_membership(
    user_id: Any,
    company_id: Any | None = None,
    *,
    roles: Collection[str] | None = None,
) -> Any:
    """Assert the actor belongs to the company; return the membership row.

    ``company_id`` defaults to the bound company context. With ``roles``,
    the membership's role must be one of them — possession of *a* role in
    one company never authorizes the *other* company's resources.
    """
    if company_id is None:
        company_id = require_company_context()

    from fastplace_tenancy.models import CompanyMembership

    membership = await CompanyMembership.where(
        CompanyMembership.user_id == user_id,
        CompanyMembership.company_id == company_id,
    ).first()
    if membership is None:
        raise NotCompanyMember(f"user {user_id!r} has no membership in company {company_id!r}")
    if roles is not None and membership.role not in roles:
        raise CompanyRoleRequired(
            f"membership role {membership.role!r} is not one of "
            f"{sorted(roles)} — company {company_id!r} requires it"
        )
    return membership


__all__ = ["require_membership", "MissingCompanyContext"]
