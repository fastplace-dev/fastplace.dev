"""Shared fixtures for the authz suite — a clean Gate and share list per test."""

from __future__ import annotations

import pytest

from fastplace.authz.gate import Gate
from fastplace.http.render import reset_shared_props


@pytest.fixture(autouse=True)
def reset_gate_and_shares():
    # Before AND after: registrations made here never leak into neighbors,
    # and neighbors' registrations never leak in.
    Gate.reset_shared()
    reset_shared_props()
    yield
    Gate.reset_shared()
    reset_shared_props()
