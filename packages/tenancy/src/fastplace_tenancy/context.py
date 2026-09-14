"""The company context — one contextvar every isolation layer reads.

The bound company is request-scoped state, exactly like the ORM's
session scope: the middleware binds it per request, ``company_context()``
binds it for scripts/tests/jobs, and everything else only *reads* it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import Any

from fastplace.errors import FastplaceError

_current_company_id: ContextVar[Any | None] = ContextVar("fastplace_company_id", default=None)


def current_company_id() -> Any | None:
    """The company bound to this task — ``None`` when no context is active."""
    return _current_company_id.get()


def require_company_context() -> Any:
    """The bound company, or :class:`MissingCompanyContext` when absent.

    Fail-closed is deliberate: a company-scoped query with no company bound
    is a missing tenant, not "every tenant" — the latter is a data breach
    wearing a default's clothing.
    """
    company_id = _current_company_id.get()
    if company_id is None:
        raise MissingCompanyContext(
            "no company context is bound — wrap the work in company_context(id) "
            "or register CompanyContextMiddleware"
        )
    return company_id


@asynccontextmanager
async def company_context(company_id: Any) -> AsyncIterator[None]:
    """Bind ``company_id`` for the block; restore the previous binding on exit.

    Nestable (an inner block shadows, the outer binding returns) and
    exception-safe — a failing inner block never leak a stale binding.
    """
    token: Token[Any | None] = _current_company_id.set(company_id)
    try:
        yield
    finally:
        _current_company_id.reset(token)


def reset_company_context() -> None:
    """Clear any binding — test isolation and CLI/boot boundaries."""
    _current_company_id.set(None)


class TenancyError(FastplaceError):
    """Base for package errors — rides the kernel's JSON error translation."""

    status_code = 400


class MissingCompanyContext(TenancyError):
    """A company-scoped operation ran with no company bound."""

    status_code = 400


class NotCompanyMember(TenancyError):
    """The actor has no membership in the target company."""

    status_code = 403


class CompanyRoleRequired(TenancyError):
    """The actor's membership lacks the role the operation demands."""

    status_code = 403


class RLSNotSupported(TenancyError):
    """The active backend has no native Row-Level Security (capability gate)."""

    status_code = 400
