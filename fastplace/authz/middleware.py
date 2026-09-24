"""Route middleware for gate checks (spec §4.15) — ``can:ability[,param]``."""

from __future__ import annotations

from typing import Any

from fastplace.authz.gate import gate
from fastplace.errors import ConfigurationError
from fastplace.http.middleware import Middleware


class CanMiddleware(Middleware):
    """Authorize the route against a gate ability before the controller runs.

    ``can:update,project`` checks ability ``update`` passing the raw STRING
    route param ``project`` as the only extra argument (model binding is a
    later concern — deviation #4). Denial raises ``AuthorizationError``
    through the kernel's JSON envelope; guests get 403, never a redirect.
    """

    def __init__(self, *args: str) -> None:
        if not args or not args[0]:
            raise ConfigurationError("can: middleware requires an ability name")
        if len(args) > 2:
            raise ConfigurationError(
                f"can: middleware takes at most ability and param name, got {list(args)}"
            )
        self.ability = args[0]
        self.param_name: str | None = args[1] if len(args) == 2 else None

    async def handle(self, request: Any, call_next: Any) -> Any:
        args: tuple[Any, ...] = ()
        if self.param_name is not None:
            if self.param_name not in request.path_params:
                raise ConfigurationError(
                    f"can:{self.ability},{self.param_name} names a param the "
                    f"route {request.full_path} does not declare"
                )
            args = (request.path_params[self.param_name],)
        await gate.authorize(request.user, self.ability, *args)
        return await call_next(request)
