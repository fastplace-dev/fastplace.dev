"""Queue isolation — the company rides in job metadata and re-binds in the worker.

Dispatch side: wrap the driver once — every dispatched job carries
``_company_id`` from the bound context (a dispatch without one fails closed,
matching the ORM).

Worker side: decorate the handler with :func:`tenant_job` — it pops the
metadata kwarg, binds the company context for the handler body, and restores
the previous binding after. Undecorated handlers receiving a tenant dispatch
fail loudly about the unexpected kwarg instead of silently running unbound.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import Any

from fastplace_tenancy.context import company_context, require_company_context

#: Kwarg carrying the tenant through the queue. The underscore prefix keeps
#: it out of handler signatures — ``@tenant_job`` strips it before the call.
COMPANY_META_KEY = "_company_id"


class TenantQueue:
    """QueueDriver wrapper stamping every dispatch with the bound company."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def dispatch(self, name: str, **kwargs: Any) -> None:
        company_id = require_company_context()
        kwargs[COMPANY_META_KEY] = company_id
        await self._inner.dispatch(name, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Everything the wrapped driver exposes (run_pending, size, …) stays
        # reachable — the wrapper only rewrites dispatch.
        return getattr(self._inner, name)


def tenant_job(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Bind the dispatched company around the handler body.

    Compose under ``@Job`` so the registry sees a plain async callable::

        @Job()
        @tenant_job
        async def rebuild_report(project_id: int): ...
    """
    if not asyncio.iscoroutinefunction(fn):
        # Fail at decoration, not at 3am in the worker — @Job's own async
        # guard must not be laundered by stacking another decorator on top.
        raise TypeError(
            f"@tenant_job needs an async def handler — {getattr(fn, '__name__', fn)!r} "
            "is synchronous; queue handlers run on the event loop"
        )

    # @Job names the handler after the function — functools.wraps carries the
    # original metadata (including __wrapped__) through.
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        company_id = kwargs.pop(COMPANY_META_KEY, None)
        if company_id is None:
            from fastplace_tenancy.context import MissingCompanyContext

            raise MissingCompanyContext(
                f"job {fn.__name__!r} was dispatched without a company — "
                "dispatch through TenantQueue"
            )
        async with company_context(company_id):
            return await fn(*args, **kwargs)

    return wrapper


__all__ = ["TenantQueue", "tenant_job", "COMPANY_META_KEY"]
