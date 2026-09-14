"""fastplace-tenancy — opt-in company-scoped multi-tenancy for Fastplace.

The core framework stays tenant-agnostic (ADR-005); installing this package
adds the ``company`` global scope, request binding, membership authorization,
and per-layer isolation (cache/queue/storage/search/vector/MongoDB/RLS).
Import paths here are ``fastplace_tenancy.*`` — the package is a sibling
distribution, never a ``fastplace.*`` submodule.
"""

from fastplace_tenancy.authorization import require_membership
from fastplace_tenancy.cache import CompanyCacheStore
from fastplace_tenancy.context import (
    CompanyRoleRequired,
    MissingCompanyContext,
    NotCompanyMember,
    RLSNotSupported,
    TenancyError,
    company_context,
    current_company_id,
    require_company_context,
    reset_company_context,
)
from fastplace_tenancy.documents import CompanyDocument
from fastplace_tenancy.middleware import CompanyContextMiddleware, request_company_id
from fastplace_tenancy.models import Company, CompanyMembership, CompanyScopedModel
from fastplace_tenancy.queue import TenantQueue, tenant_job
from fastplace_tenancy.rls import (
    clear_rls_company,
    enable_company_rls,
    set_rls_company,
    supports_rls,
)
from fastplace_tenancy.search import TenantSearchService
from fastplace_tenancy.storage import (
    company_root,
    company_storage_path,
    ensure_company_path,
)
from fastplace_tenancy.vectors import TenantVectorStore

__all__ = [
    # context
    "CompanyRoleRequired",
    "MissingCompanyContext",
    "NotCompanyMember",
    "RLSNotSupported",
    "TenancyError",
    "company_context",
    "current_company_id",
    "require_company_context",
    "reset_company_context",
    # models + authorization
    "Company",
    "CompanyMembership",
    "CompanyScopedModel",
    "require_membership",
    # middleware
    "CompanyContextMiddleware",
    "request_company_id",
    # cache
    "CompanyCacheStore",
    # queue
    "TenantQueue",
    "tenant_job",
    # storage
    "company_root",
    "company_storage_path",
    "ensure_company_path",
    # search / vectors / documents
    "TenantSearchService",
    "TenantVectorStore",
    "CompanyDocument",
    # RLS
    "clear_rls_company",
    "enable_company_rls",
    "set_rls_company",
    "supports_rls",
]
