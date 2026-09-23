"""Shared fixtures for the authz suite — a clean shared Gate per test."""

from __future__ import annotations

import pytest

from fastplace.authz.gate import Gate


@pytest.fixture(autouse=True)
def reset_gate():
    # Before AND after: registrations made here never leak into neighbors,
    # and neighbors' registrations never leak in.
    Gate.reset_shared()
    yield
    Gate.reset_shared()
