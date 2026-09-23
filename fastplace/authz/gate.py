"""Gate — the authorization check surface (spec §4.15).

``gate.define`` registers async ability callbacks, ``gate.policy`` binds
policy classes to models, and ``gate.before`` installs a global hook that
always runs first. Checks resolve in this order:

1. the global ``before`` hook — True/False are final, None falls through;
2. an exact ability callback registered via ``define``;
3. the policy bound to the first extra argument's class (explicit bind or
   ``app/modules/<module>/policies`` auto-discovery) — the policy's own
   ``before()`` runs before its action method;
4. otherwise ``ConfigurationError`` — an unknown ability is a developer
   error, never a silent deny.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Coroutine
from typing import Any

from fastplace.authz.response import Response
from fastplace.errors import ConfigurationError

GateCallback = Callable[..., Coroutine[Any, Any, bool | Response]]
BeforeCallback = Callable[..., Coroutine[Any, Any, bool | Response | None]]


def _truthy_verdict(result: bool | Response | None) -> Response | None:
    """Normalize a hook/callback answer; None means "no opinion"."""
    if result is None:
        return None
    if isinstance(result, Response):
        return result
    return Response.allow() if result else Response.deny()


def _final_verdict(verdict: Response | None) -> Response:
    """A callback that broke contract and returned None still means deny."""
    return verdict if verdict is not None else Response.deny()


class BoundGate:
    """A gate view with the user pre-bound (``gate.for_user(user)``)."""

    def __init__(self, gate: Gate, user: Any) -> None:
        self._gate = gate
        self._user = user

    def allows(self, ability: str, *args: Any) -> Coroutine[Any, Any, bool]:
        return self._gate.allows(self._user, ability, *args)

    def denies(self, ability: str, *args: Any) -> Coroutine[Any, Any, bool]:
        return self._gate.denies(self._user, ability, *args)

    def check(self, ability: str, *args: Any) -> Coroutine[Any, Any, Response]:
        return self._gate.check(self._user, ability, *args)

    def authorize(self, ability: str, *args: Any) -> Coroutine[Any, Any, None]:
        return self._gate.authorize(self._user, ability, *args)

    def any(self, abilities: list[str], *args: Any) -> Coroutine[Any, Any, bool]:
        return self._gate.any(self._user, abilities, *args)

    def none(self, abilities: list[str], *args: Any) -> Coroutine[Any, Any, bool]:
        return self._gate.none(self._user, abilities, *args)

    def inspect(self, ability: str, *args: Any) -> Coroutine[Any, Any, dict]:
        return self._gate.inspect(self._user, ability, *args)


class Gate:
    """The authorization registry and async check engine."""

    def __init__(self) -> None:
        self._abilities: dict[str, GateCallback] = {}
        self._policies: dict[type, type] = {}
        self._before: BeforeCallback | None = None
        self._discovered: set[type] = set()

    # -- registration -------------------------------------------------

    def define(self, ability: str) -> Callable[[GateCallback], GateCallback]:
        def decorator(fn: GateCallback) -> GateCallback:
            self._abilities[ability] = fn
            return fn

        return decorator

    def before(self, fn: BeforeCallback) -> BeforeCallback:
        self._before = fn
        return fn

    def policy(self, model_cls: type, policy_cls: type) -> type:
        self._policies[model_cls] = policy_cls
        return policy_cls

    @classmethod
    def reset_shared(cls) -> None:
        """Clear the shared singleton's registries — the test seam."""
        shared.reset()

    def reset(self) -> None:
        self._abilities.clear()
        self._policies.clear()
        self._discovered.clear()
        self._before = None

    # -- resolution ---------------------------------------------------

    def _resolve_policy(self, model_cls: type) -> type | None:
        explicit = self._policies.get(model_cls)
        if explicit is not None:
            return explicit
        # Cache only the failures — a successful discovery must keep
        # resolving on every later check.
        if model_cls in self._discovered:
            return None
        # app/modules/<name>/models/<model>.py -> app/modules/<name>/policies
        module = model_cls.__module__
        parts = module.rsplit(".models", 1)
        if len(parts) == 2:
            candidate = f"{parts[0]}.policies{parts[1]}"
            for target in (candidate, f"{parts[0]}.policies"):
                try:
                    policies_module = importlib.import_module(target)
                except ImportError:
                    continue
                policy_cls = getattr(policies_module, f"{model_cls.__name__}Policy", None)
                if policy_cls is not None:
                    return policy_cls
        self._discovered.add(model_cls)
        return None

    async def _check_with_source(
        self, user: Any, ability: str, args: tuple[Any, ...]
    ) -> tuple[Response, str]:
        if self._before is not None:
            verdict = _truthy_verdict(await self._before(user, ability, *args))
            if verdict is not None:
                return verdict, "before"

        callback = self._abilities.get(ability)
        if callback is not None:
            verdict = _truthy_verdict(await callback(user, *args))
            return _final_verdict(verdict), "ability"

        if args:
            policy_cls = self._resolve_policy(type(args[0]))
            if policy_cls is not None:
                policy = policy_cls()
                hook = getattr(policy, "before", None)
                if hook is not None:
                    verdict = _truthy_verdict(await hook(user, ability, *args))
                    if verdict is not None:
                        return verdict, "before"
                method = getattr(policy, ability, None)
                if method is None or not inspect.iscoroutinefunction(method):
                    raise ConfigurationError(
                        f"policy {policy_cls.__name__} has no async method "
                        f"{ability!r} for ability {ability!r}"
                    )
                # The policy contract is (user, model) — trailing extra args
                # are context for the hooks, never for the action method.
                verdict = _truthy_verdict(await method(user, args[0]))
                return _final_verdict(verdict), "policy"

        raise ConfigurationError(
            f"ability {ability!r} is not defined — register it with "
            "gate.define or bind a policy for the argument's model"
        )

    # -- public check surface -----------------------------------------

    async def check(self, user: Any, ability: str, *args: Any) -> Response:
        verdict, _source = await self._check_with_source(user, ability, args)
        return verdict

    async def allows(self, user: Any, ability: str, *args: Any) -> bool:
        return bool(await self.check(user, ability, *args))

    async def denies(self, user: Any, ability: str, *args: Any) -> bool:
        return not await self.allows(user, ability, *args)

    async def authorize(self, user: Any, ability: str, *args: Any) -> None:
        verdict = await self.check(user, ability, *args)
        error = verdict.as_error()
        if error is not None:
            raise error

    async def any(self, user: Any, abilities: list[str], *args: Any) -> bool:
        for ability in abilities:
            if await self.allows(user, ability, *args):
                return True
        return False

    async def none(self, user: Any, abilities: list[str], *args: Any) -> bool:
        return not await self.any(user, abilities, *args)

    async def inspect(self, user: Any, ability: str, *args: Any) -> dict:
        verdict, source = await self._check_with_source(user, ability, args)
        return {
            "ability": ability,
            "allowed": bool(verdict),
            "source": source,
            "message": verdict.message,
        }

    def for_user(self, user: Any) -> BoundGate:
        return BoundGate(self, user)


# The shared singleton applications register against (app/auth/gates.py).
shared = Gate()
gate = shared
