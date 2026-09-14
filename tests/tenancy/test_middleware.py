"""CompanyContextMiddleware — binding the request to one company.

Registration order mirrors the auth stack: ResolveUserMiddleware first (the
default resolution reads ``request.user``), then this middleware, then CSRF.
The bound company is exposed both as the contextvar (services, ORM scope)
and on the request (``request_company_id``) for controller use.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from fastplace.http.kernel import get_app
from fastplace.http.middleware import Middleware
from fastplace.http.router import Router

USER = SimpleNamespace(id=7, name="Firoz")


class _SetUserMiddleware(Middleware):
    """Test double for ResolveUserMiddleware — pins request.user."""

    async def handle(self, request, call_next):
        request.set_user(USER)
        return await call_next(request)


def build_app() -> object:
    from fastplace_tenancy.middleware import CompanyContextMiddleware

    class WhoamiController:
        async def index(self, request):
            from fastplace_tenancy import current_company_id
            from fastplace_tenancy.middleware import request_company_id

            return {
                "company": current_company_id(),
                "on_request": request_company_id(request),
            }

    class SwitchController:
        async def store(self, request):
            body = await request.json()
            request.session["company_id"] = body["company_id"]
            return {"switched": True}

    router = Router()
    router.get("/whoami", WhoamiController, "index", name="whoami")
    router.post("/switch", SwitchController, "store", name="switch")
    return get_app(
        routes=router,
        middleware=[_SetUserMiddleware(), CompanyContextMiddleware()],
        config={
            "APP_ENV": "local",
            "APP_KEY": "test-app-key-not-for-production-use-only",
        },
    )


@pytest.fixture()
async def client(backend):
    from fastplace_tenancy.models import Company, CompanyMembership

    from fastplace.db import db

    await db.create_all()
    acme = await Company.create(name="Acme")
    globex = await Company.create(name="Globex")
    await CompanyMembership.create(company_id=acme.id, user_id=USER.id, role="owner")

    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, acme, globex


async def test_membership_resolves_the_default_company(client):
    c, acme, _ = client
    response = await c.get("/whoami")
    assert response.status_code == 200
    assert response.json()["company"] == acme.id


async def test_session_switch_is_validated_against_membership(client):
    """The session value is a *request*, not a grant — switching to a company
    the user does not belong to falls back to their real membership instead
    of binding them anywhere."""
    c, acme, globex = client
    switched = await c.post("/switch", json={"company_id": globex.id})
    assert switched.status_code == 200

    response = await c.get("/whoami")
    assert response.json()["company"] == acme.id


async def test_session_switch_to_a_member_company_binds(client):
    c, acme, globex = client
    from fastplace_tenancy.models import CompanyMembership

    await CompanyMembership.create(company_id=globex.id, user_id=USER.id, role="member")
    switched = await c.post("/switch", json={"company_id": globex.id})
    assert switched.status_code == 200
    response = await c.get("/whoami")
    assert response.json()["company"] == globex.id


async def test_default_binding_is_deterministic(backend):
    """A user with several memberships binds the earliest one — every time,
    not whatever the planner happens to return first (an unordered first()
    can flap between requests; the binding must be a documented key)."""
    from fastplace_tenancy.middleware import CompanyContextMiddleware
    from fastplace_tenancy.models import Company, CompanyMembership

    from fastplace.db import db

    await db.create_all()
    globex = await Company.create(name="Globex")
    acme = await Company.create(name="Acme")
    # Globex membership is the EARLIEST (joined first) — that is the rule,
    # independent of row order on disk.
    await CompanyMembership.create(company_id=globex.id, user_id=9, role="member")
    await CompanyMembership.create(company_id=acme.id, user_id=9, role="owner")

    class PinUserMiddleware(Middleware):
        async def handle(self, request, call_next):
            request.set_user(SimpleNamespace(id=9))
            return await call_next(request)

    class WhoamiController:
        async def index(self, request):
            from fastplace_tenancy import current_company_id

            return {"company": current_company_id()}

    router = Router()
    router.get("/whoami", WhoamiController, "index", name="whoami2")
    app = get_app(
        routes=router,
        middleware=[PinUserMiddleware(), CompanyContextMiddleware()],
        config={"APP_ENV": "local", "APP_KEY": "test-app-key-not-for-production-use-only"},
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        for _ in range(3):
            assert (await c.get("/whoami")).json()["company"] == globex.id


async def test_request_helper_carries_the_same_binding(client):
    c, acme, _ = client
    response = await c.get("/whoami")
    body = response.json()
    assert body["on_request"] == acme.id
    assert body["on_request"] == body["company"]


async def test_anonymous_request_leaves_context_unbound(client):
    """No user, no session key — the context stays None (queries fail closed)."""
    from fastplace_tenancy.middleware import CompanyContextMiddleware

    class ProbeController:
        async def index(self, request):
            from fastplace_tenancy import current_company_id

            return {"company": current_company_id()}

    router = Router()
    router.get("/probe", ProbeController, "index", name="probe")
    app = get_app(
        routes=router,
        middleware=[CompanyContextMiddleware()],
        config={
            "APP_ENV": "local",
            "APP_KEY": "test-app-key-not-for-production-use-only",
        },
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c2:
        response = await c2.get("/probe")
        assert response.json()["company"] is None


async def test_custom_resolution_hook(backend):
    """Subclasses source the company however the app wants (subdomain, JWT…)."""

    from fastplace_tenancy.middleware import CompanyContextMiddleware

    class SubdomainMiddleware(CompanyContextMiddleware):
        async def _resolve_company_id(self, request):  # the overridable hook
            return 42

    class ProbeController:
        async def index(self, request):
            from fastplace_tenancy import current_company_id

            return {"company": current_company_id()}

    router = Router()
    router.get("/probe", ProbeController, "index", name="probe")
    app = get_app(
        routes=router,
        middleware=[SubdomainMiddleware()],
        config={
            "APP_ENV": "local",
            "APP_KEY": "test-app-key-not-for-production-use-only",
        },
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/probe")).json()["company"] == 42
