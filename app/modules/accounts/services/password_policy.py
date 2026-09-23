"""Password policy — one source of truth for the PASSWORD_RULES dialects.

``config/auth.py`` speaks the server dialect (``min:8``); the ported React
pages consume the frontend dialect (``minlength: 8;``) for the HTML
``passwordrules`` attribute. Phase 2 honors only the ``min:N`` clause —
richer clauses arrive with the settings UI.
"""

from __future__ import annotations

import re

from fastplace.config import config

DEFAULT_RULES = "min:8"


def configured_rules() -> str:
    """The raw PASSWORD_RULES string (server dialect)."""
    return str(config("PASSWORD_RULES", default=DEFAULT_RULES) or DEFAULT_RULES)


def min_password_length(rules: str | None = None) -> int:
    """The ``min:N`` clause as an int (default 8 when absent or unparsable)."""
    raw = configured_rules() if rules is None else rules
    match = re.search(r"(?:^|,)min:(\d+)", raw)
    return int(match.group(1)) if match else 8


def frontend_rules() -> str:
    """PASSWORD_RULES translated to the bridge pages' dialect (spec §4.9)."""
    return f"minlength: {min_password_length()};"
