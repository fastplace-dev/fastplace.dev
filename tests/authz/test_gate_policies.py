"""Policy classes — explicit bind, before(), auto-discovery (spec §4.15)."""

from __future__ import annotations

import sys
import types

import pytest

from fastplace.authz import gate
from fastplace.errors import ConfigurationError


class Project:
    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id


class User:
    def __init__(self, id: int, admin: bool = False) -> None:
        self.id = id
        self.admin = admin


class ProjectPolicy:
    async def update(self, user, project):
        return project.owner_id == user.id

    async def delete(self, user, project):
        if user.admin:
            return True
        return project.owner_id == user.id

    async def view(self, user, project):
        # Public read — even guests.
        return True


class TestExplicitPolicy:
    async def test_method_per_action_with_user_first(self):
        gate.policy(Project, ProjectPolicy)
        owner = User(id=1)
        other = User(id=2)

        assert await gate.allows(owner, "update", Project(owner_id=1)) is True
        assert await gate.allows(other, "update", Project(owner_id=1)) is False

    async def test_guest_flows_through_policy_default_deny(self):
        gate.policy(Project, ProjectPolicy)
        # view() allows guests; update() compares owner_id and would crash on
        # None — a policy author denies guests explicitly. That contract is
        # the author's; the gate adds no magic.
        assert await gate.allows(None, "view", Project(owner_id=1)) is True

    async def test_missing_policy_method_raises_configuration_error(self):
        gate.policy(Project, ProjectPolicy)
        with pytest.raises(ConfigurationError, match="export"):
            await gate.allows(User(id=1), "export", Project(owner_id=1))

    async def test_policy_before_always_runs(self):
        class Snippet:
            pass

        class SnippetPolicy:
            async def before(self, user, ability, *args):
                if user is not None and user.admin:
                    return True
                return None

            async def update(self, user, snippet):
                return False

        gate.policy(Snippet, SnippetPolicy)
        assert await gate.allows(User(id=1, admin=True), "update", Snippet()) is True
        assert await gate.allows(User(id=1), "update", Snippet()) is False

    async def test_policy_route_uses_the_first_extra_args_class(self):
        gate.policy(Project, ProjectPolicy)
        # A second extra arg never confuses the policy route.
        assert await gate.allows(User(id=1), "update", Project(owner_id=1), "ctx") is True


class TestAutoDiscovery:
    async def test_discovers_model_module_policies_package(self, monkeypatch):
        # Simulate app/modules/projects/models/project.py declaring Project,
        # with app/modules/projects/policies.py exporting ProjectPolicy — the
        # attribute name on the policies module is f"{Model.__name__}Policy".
        model_module = types.ModuleType("app.modules.projects.models")
        policies_module = types.ModuleType("app.modules.projects.policies")

        class DiscoveredProject:
            __module__ = model_module.__name__

        class DiscoveredProjectPolicy:
            async def update(self, user, project):
                return True

        policies_module.DiscoveredProjectPolicy = DiscoveredProjectPolicy

        monkeypatch.setitem(sys.modules, model_module.__name__, model_module)
        monkeypatch.setitem(sys.modules, "app.modules.projects.policies", policies_module)

        assert await gate.allows(User(id=1), "update", DiscoveredProject()) is True

    async def test_auto_discovery_resolves_on_repeated_checks(self, monkeypatch):
        # The failure cache must not swallow a successful discovery on retry —
        # production re-checks the same model on every request.
        model_module = types.ModuleType("app.modules.projects.models")
        policies_module = types.ModuleType("app.modules.projects.policies")

        class RepeatProject:
            __module__ = model_module.__name__

        class RepeatProjectPolicy:
            async def update(self, user, project):
                return True

        policies_module.RepeatProjectPolicy = RepeatProjectPolicy

        monkeypatch.setitem(sys.modules, model_module.__name__, model_module)
        monkeypatch.setitem(sys.modules, "app.modules.projects.policies", policies_module)

        assert await gate.allows(User(id=1), "update", RepeatProject()) is True
        assert await gate.allows(User(id=1), "update", RepeatProject()) is True

    async def test_discovery_failure_falls_through_to_configuration_error(self):
        class Orphan:
            __module__ = "app.modules.ghost.models"

        with pytest.raises(ConfigurationError, match="is not defined"):
            await gate.allows(User(id=1), "update", Orphan())

    async def test_explicit_bind_beats_auto_discovery(self, monkeypatch):
        model_module = types.ModuleType("app.modules.projects.models")
        policies_module = types.ModuleType("app.modules.projects.policies")

        class BoundProject:
            __module__ = model_module.__name__

        class AutoPolicy:
            async def update(self, user, project):
                return False

        class ExplicitPolicy:
            async def update(self, user, project):
                return True

        policies_module.BoundProjectPolicy = AutoPolicy
        monkeypatch.setitem(sys.modules, model_module.__name__, model_module)
        monkeypatch.setitem(sys.modules, "app.modules.projects.policies", policies_module)

        gate.policy(BoundProject, ExplicitPolicy)
        assert await gate.allows(User(id=1), "update", BoundProject()) is True
