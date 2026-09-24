"""Gate ability checks, before-hooks, and error mapping (spec §4.15)."""

from __future__ import annotations

import pytest

from fastplace.authz import Response, gate
from fastplace.errors import AuthorizationError, ConfigurationError


class User:
    def __init__(self, admin: bool = False) -> None:
        self.admin = admin


ADMIN = User(admin=True)
MEMBER = User()
GUEST = None


class TestDefine:
    async def test_bool_callback_answers_allows_and_denies(self):
        @gate.define("view-dashboard")
        async def view_dashboard(user, *args):
            return user is not None

        assert await gate.allows(MEMBER, "view-dashboard") is True
        assert await gate.allows(GUEST, "view-dashboard") is False
        assert await gate.denies(GUEST, "view-dashboard") is True

    async def test_response_callback_passes_message_through_check(self):
        @gate.define("update-project")
        async def update_project(user, project):
            return Response.deny("You do not own this project.")

        verdict = await gate.check(MEMBER, "update-project", "proj-1")
        assert bool(verdict) is False
        assert verdict.message == "You do not own this project."

    async def test_extra_args_reach_the_callback_verbatim(self):
        seen: list[object] = []

        @gate.define("bulk-delete")
        async def bulk_delete(user, *args):
            seen.extend(args)
            return True

        await gate.allows(ADMIN, "bulk-delete", "a", 7, None)
        assert seen == ["a", 7, None]

    async def test_unknown_ability_raises_configuration_error(self):
        with pytest.raises(ConfigurationError, match="not-a-thing"):
            await gate.allows(ADMIN, "not-a-thing")


class TestAuthorize:
    async def test_allow_returns_none(self):
        @gate.define("ping")
        async def ping(user, *args):
            return True

        assert await gate.authorize(ADMIN, "ping") is None

    async def test_deny_raises_with_the_default_403(self):
        @gate.define("reboot")
        async def reboot(user, *args):
            return False

        with pytest.raises(AuthorizationError) as exc_info:
            await gate.authorize(MEMBER, "reboot")
        assert exc_info.value.status_code == 403
        assert exc_info.value.message == "This action is unauthorized."

    async def test_deny_as_not_found_raises_a_404(self):
        @gate.define("read-invoice")
        async def read_invoice(user, invoice):
            return Response.deny_as_not_found()

        with pytest.raises(AuthorizationError) as exc_info:
            await gate.authorize(MEMBER, "read-invoice", "inv-9")
        assert exc_info.value.status_code == 404


class TestBefore:
    async def test_true_short_circuits_to_allow(self):
        @gate.define("reboot")
        async def reboot(user, *args):
            return False  # would deny — before must win

        @gate.before
        async def superuser(user, ability, *args):
            return user is not None and user.admin

        assert await gate.allows(ADMIN, "reboot") is True

    async def test_false_short_circuits_to_deny(self):
        @gate.define("anything")
        async def anything(user, *args):
            return True  # would allow — before must win

        @gate.before
        async def banned(user, ability, *args):
            return False

        assert await gate.allows(ADMIN, "anything") is False

    async def test_none_falls_through_to_the_ability(self):
        @gate.define("open-door")
        async def open_door(user, *args):
            return True

        @gate.before
        async def indifferent(user, ability, *args):
            return None

        assert await gate.allows(MEMBER, "open-door") is True

    async def test_before_may_allow_guests(self):
        @gate.define("read-post")
        async def read_post(user, post):
            return False

        @gate.before
        async def public_reads(user, ability, *args):
            if ability.startswith("read-"):
                return True
            return None

        assert await gate.allows(GUEST, "read-post", "p1") is True


class TestAnyNone:
    async def test_any_short_circuits_on_first_allow(self):
        calls: list[str] = []

        @gate.define("edit-post")
        async def edit_post(user, *args):
            calls.append("edit")
            return False

        @gate.define("moderate-post")
        async def moderate_post(user, *args):
            calls.append("moderate")
            return user is not None and user.admin

        assert await gate.any(ADMIN, ["edit-post", "moderate-post"]) is True
        assert calls == ["edit", "moderate"]

    async def test_none_is_the_negation_of_any(self):
        @gate.define("a")
        async def a(user, *args):
            return False

        @gate.define("b")
        async def b(user, *args):
            return False

        assert await gate.none(ADMIN, ["a", "b"]) is True
        assert await gate.none(ADMIN, ["a"]) is True


class TestInspectAndForUser:
    async def test_inspect_reports_source_and_message(self):
        @gate.define("delete-post")
        async def delete_post(user, post):
            return Response.deny("Not yours.")

        report = await gate.inspect(MEMBER, "delete-post", "p1")
        assert report == {
            "ability": "delete-post",
            "allowed": False,
            "source": "ability",
            "message": "Not yours.",
        }

    async def test_inspect_reports_before_source(self):
        @gate.before
        async def superuser(user, ability, *args):
            return user is not None and user.admin

        report = await gate.inspect(ADMIN, "whatever")
        assert report["source"] == "before"
        assert report["allowed"] is True

    async def test_for_user_binds_the_user(self):
        @gate.define("ship")
        async def ship(user, *args):
            return user is not None and user.admin

        admin_gate = gate.for_user(ADMIN)
        member_gate = gate.for_user(MEMBER)
        assert await admin_gate.allows("ship") is True
        assert await member_gate.allows("ship") is False
        assert await member_gate.denies("ship") is True


class TestResetShared:
    async def test_reset_clears_registrations(self):
        @gate.define("temp")
        async def temp(user, *args):
            return True

        assert await gate.allows(ADMIN, "temp") is True
        gate.reset_shared()
        with pytest.raises(ConfigurationError):
            await gate.allows(ADMIN, "temp")
