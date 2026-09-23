"""The controller-facing authorization entry point (spec §4.15)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastplace.http.request import Request


async def authorize(request: Request, ability: str, *args: Any) -> None:
    """Raise ``AuthorizationError`` unless the ability holds for request.user.

    Controllers call this at the top of an action; the gate owns every
    decision, the helper only carries the request's user and arguments.
    """
    from fastplace.authz import gate

    await gate.authorize(request.user, ability, *args)
