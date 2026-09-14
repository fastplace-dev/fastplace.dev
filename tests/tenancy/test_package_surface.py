"""The package surface — every public layer entry point from one import.

The README documents ``from fastplace_tenancy import …`` for all layers; this
test keeps the root re-export honest so user code never has to know module
paths.
"""

from __future__ import annotations


def test_root_exports_every_layer():
    import fastplace_tenancy

    expected = [
        # context + models + authorization
        "Company",
        "CompanyMembership",
        "CompanyScopedModel",
        "company_context",
        "current_company_id",
        "require_company_context",
        "require_membership",
        "MissingCompanyContext",
        "NotCompanyMember",
        "CompanyRoleRequired",
        "TenancyError",
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
        "enable_company_rls",
        "supports_rls",
        "set_rls_company",
        "clear_rls_company",
        "RLSNotSupported",
    ]
    missing = [name for name in expected if not hasattr(fastplace_tenancy, name)]
    assert missing == []
    # Everything exported is also declared in __all__ (dir() stays honest).
    declared = set(fastplace_tenancy.__all__)
    undeclared = [name for name in expected if name not in declared]
    assert undeclared == []
