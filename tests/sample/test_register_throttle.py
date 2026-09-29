"""The register route's throttle is a named limiter with an env-carriable max.

Numeric ``throttle:5,60`` pins the limit at route-declaration time, so the
e2e suite — which legitimately registers more users in a minute than one
human ever would, all from 127.0.0.1 — cannot be given headroom without
editing route code. The named ``throttle:register`` limiter keeps the
production default (5/min, fail-closed) while AUTH_REGISTER_THROTTLE_MAX
lets a test topology raise it.
"""

from __future__ import annotations

from fastplace import ratelimit
from fastplace.ratelimit import Limit


def _register_route():
    from routes.auth import router

    for route in router.routes:
        if getattr(route, "name", None) == "auth.register.store":
            return route
    raise AssertionError("auth.register.store route missing")


def test_register_route_uses_the_named_limiter():
    # A numeric 5,60 here would pin the limit beyond config's reach.
    assert "throttle:register" in _register_route().middleware


def test_register_limiter_defaults_to_five_per_minute(monkeypatch):
    import config.app  # noqa: F401 — registration runs at import

    monkeypatch.delenv("AUTH_REGISTER_THROTTLE_MAX", raising=False)
    assert "register" in ratelimit.registered_limits()
    limit = ratelimit._limiters["register"](None)
    assert isinstance(limit, Limit)
    assert limit.max_attempts == 5
    assert limit.decay == 60


def test_register_limiter_max_follows_the_env(monkeypatch):
    import config.app  # noqa: F401

    monkeypatch.setenv("AUTH_REGISTER_THROTTLE_MAX", "20")
    limit = ratelimit._limiters["register"](None)
    assert limit.max_attempts == 20
    assert limit.decay == 60
