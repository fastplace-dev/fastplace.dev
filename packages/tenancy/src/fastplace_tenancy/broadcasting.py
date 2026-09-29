"""Company-scoped broadcasting — channel prefixing + the membership vow.

Core broadcasting stays tenant-agnostic (ADR-005): it parses
``company.{id}.`` channel prefixes but never decides what membership means.
This module is the tenancy side of the seam — :func:`tenant_channel`
builds company-scoped names from the bound context (the TenantQueue
pattern), and :func:`tenant_channel_authorizer` vouches for (or refuses) a
company channel at subscribe time by checking the requesting user's
membership row. The websocket scope has no CompanyContextMiddleware, so
the company id comes from the parsed channel itself.
"""

from __future__ import annotations

from typing import Any

from fastplace_tenancy.context import require_company_context

__all__ = ["install_tenant_broadcasting", "tenant_channel", "tenant_channel_authorizer"]


def tenant_channel(name: str) -> str:
    """Prefix ``name`` with the bound company: ``company.{id}.{name}``."""
    company_id = require_company_context()
    return f"company.{company_id}.{name}"


async def tenant_channel_authorizer(user: Any, channel: Any) -> bool | None:
    """The ChannelAuthorizer for ``company.{id}.`` channels.

    Returns ``None`` (abstain) for non-company channels so core's own
    public/private rules keep deciding those; for company channels it is a
    plain membership verdict — anonymous or non-members are denied.
    """
    if channel.tenant_id is None:
        return None
    if user is None:
        return False
    from fastplace.auth.providers import user_identifier
    from fastplace_tenancy.models import CompanyMembership

    membership = await CompanyMembership.where(
        CompanyMembership.user_id == user_identifier(user),
        CompanyMembership.company_id == channel.tenant_id,
    ).first()
    return membership is not None


def install_tenant_broadcasting() -> None:
    """Register the tenant channel authorizer with core broadcasting.

    Call once at boot (the tenancy package's own install point or app
    startup); the authorizer chain runs at every subscribe.
    """
    from fastplace.broadcasting import register_channel_authorizer

    register_channel_authorizer(tenant_channel_authorizer)
