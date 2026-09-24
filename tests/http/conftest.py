"""Isolation for the http suite — shared props stay per-test.

``get_app`` never registers production shares, but ``create_app`` boot tests
do; reset before and after so registrations never cross test boundaries.
"""

from __future__ import annotations

import pytest

from fastplace.http.render import reset_shared_props


@pytest.fixture(autouse=True)
def _clean_shared_props():
    reset_shared_props()
    yield
    reset_shared_props()
