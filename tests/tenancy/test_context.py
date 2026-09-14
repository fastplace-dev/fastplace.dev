"""Company context — the contextvar every isolation layer reads."""

from __future__ import annotations

import pytest


def test_no_company_by_default():
    from fastplace_tenancy.context import current_company_id

    assert current_company_id() is None


async def test_company_context_binds_and_restores():
    from fastplace_tenancy.context import company_context, current_company_id

    async with company_context(7):
        assert current_company_id() == 7
        async with company_context(9):
            assert current_company_id() == 9
        assert current_company_id() == 7
    assert current_company_id() is None


async def test_nested_context_restore_survives_exceptions():
    from fastplace_tenancy.context import company_context, current_company_id

    async with company_context(1):
        with pytest.raises(RuntimeError, match="boom"):
            async with company_context(2):
                raise RuntimeError("boom")
        assert current_company_id() == 1


def test_require_company_context_explains_when_missing():
    from fastplace_tenancy.context import (
        MissingCompanyContext,
        require_company_context,
    )

    with pytest.raises(MissingCompanyContext, match="company context"):
        require_company_context()


def test_missing_company_context_is_a_fastplace_error():
    """Package errors ride the kernel's JSON error translation (403/4xx, not 500)."""
    from fastplace_tenancy.context import MissingCompanyContext

    from fastplace.errors import FastplaceError

    assert issubclass(MissingCompanyContext, FastplaceError)
