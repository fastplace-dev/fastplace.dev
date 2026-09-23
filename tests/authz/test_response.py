"""Response verdicts and their AuthorizationError mapping (spec §4.15)."""

from __future__ import annotations

import pytest

from fastplace.authz.response import Response
from fastplace.errors import AuthorizationError


class TestResponseVerdicts:
    def test_allow_is_truthy_and_carries_no_message(self):
        verdict = Response.allow()
        assert bool(verdict) is True
        assert verdict.message is None
        assert verdict.status_code is None

    def test_deny_is_falsy_and_carries_its_message(self):
        verdict = Response.deny("You do not own this project.")
        assert bool(verdict) is False
        assert verdict.message == "You do not own this project."
        assert verdict.status_code is None

    def test_deny_accepts_status_and_code(self):
        verdict = Response.deny("Locked", status_code=423, code="project_locked")
        assert verdict.status_code == 423
        assert verdict.code == "project_locked"

    def test_deny_as_not_found_masks_as_a_real_404(self):
        verdict = Response.deny_as_not_found()
        assert bool(verdict) is False
        assert verdict.status_code == 404
        assert verdict.message == "Resource not found."


class TestResponseAsError:
    def test_as_error_maps_deny_onto_the_default_403(self):
        error = Response.deny("nope").as_error()
        assert isinstance(error, AuthorizationError)
        assert error.message == "nope"
        assert error.status_code == 403

    def test_as_error_carries_status_code_and_code(self):
        error = Response.deny_as_not_found().as_error()
        assert error.status_code == 404
        assert error.message == "Resource not found."

        locked = Response.deny("Locked", status_code=423, code="project_locked").as_error()
        assert locked.status_code == 423
        assert locked.code == "project_locked"

    def test_allow_as_error_never_raises(self):
        # allow() has no error to build — returns None by contract.
        assert Response.allow().as_error() is None


class TestAuthorizationErrorSignature:
    def test_positional_extras_are_rejected(self):
        # status_code/code are keyword-only by contract — a positional extra
        # (likely a stray message argument) must fail loudly.
        with pytest.raises(TypeError):
            AuthorizationError("m", 404)
