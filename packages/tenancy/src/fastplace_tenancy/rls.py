"""Row-Level Security — push the tenant policy into PostgreSQL itself.

Defense-in-depth *below* the ORM scope: even a query that escapes the
``company`` global scope meets a database-level policy. Genuinely
database-dependent (blueprint §8): PostgreSQL offers native RLS; MySQL and
SQLite never claim it — :func:`supports_rls` reads the capability registry,
and the helpers refuse rather than emulate.

Deployment notes (the parts DDL cannot do for you):

- the connecting role must not own the table or carry ``BYPASSRLS`` —
  owners bypass policies silently;
- each transaction must ``SET LOCAL app.company_id`` before touching company
  tables (:func:`set_rls_company` does this on the ambient transaction —
  pool-friendly precisely because the setting dies with the transaction and
  never leaks to the connection's next borrower).
"""

from __future__ import annotations

from typing import Any

from fastplace_tenancy.context import RLSNotSupported

_POLICY_NAME = "fastplace_company_isolation"
_SETTING = "app.company_id"


def supports_rls() -> bool:
    """Whether the active backend offers native Row-Level Security."""
    from fastplace.db import db

    return bool(db.capabilities.supports("row_level_security"))


async def enable_company_rls(model: Any) -> None:
    """Enable RLS on the model's table with the company policy.

    Idempotent: the policy is dropped and recreated (PostgreSQL has no
    ``CREATE POLICY IF NOT EXISTS``).
    """
    _require_rls()
    table = model.__tablename__
    from fastplace.db import db

    await db.raw(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    await db.raw(f'DROP POLICY IF EXISTS "{_POLICY_NAME}" ON "{table}"')
    # A bare "app.company_id" in the expression would parse as table "app",
    # column "company_id" — custom GUCs must go through current_setting().
    # NULLIF, not "IS NOT NULL": an unset custom GUC returns NULL with
    # missing_ok, but a *rolled-back or cleared* one leaves an empty-string
    # placeholder — ''::int would error the whole query. NULLIF maps both
    # flavours of "not set" to NULL → policy false → zero rows, the same
    # fail-closed posture as the ORM scope.
    await db.raw(
        f'CREATE POLICY "{_POLICY_NAME}" ON "{table}" USING '
        f"(company_id = NULLIF(current_setting('{_SETTING}', true), '')::int)"
    )


async def set_rls_company(company_id: Any) -> None:
    """Point the connection's RLS setting at ``company_id`` (ambient transaction).

    ``SET LOCAL``, never plain ``SET``: the setting dies with the transaction,
    so a pooled connection carries nothing into the next borrower's
    transaction. A session-level SET survives COMMIT and would silently run
    the next request's queries under *this* company.
    """
    _require_rls()
    from fastplace.orm.session import current_session

    session = current_session()
    if session is None:
        raise RLSNotSupported(
            "no ambient session — set the RLS company inside a session scope "
            "(request or transaction)"
        )
    from sqlalchemy import text

    value = "" if company_id is None else str(int(company_id))
    await session.execute(text(f"SET LOCAL {_SETTING} = '{value}'"))


async def clear_rls_company() -> None:
    """Unset the setting — the policy then matches nothing (fail closed)."""
    await set_rls_company(None)


def _require_rls() -> None:
    if not supports_rls():
        from fastplace.db import db

        raise RLSNotSupported(
            f"backend {db.capabilities.driver!r} has no native Row-Level "
            "Security — isolation stays at the ORM/application layer "
            "(the capability registry never claims it)"
        )
