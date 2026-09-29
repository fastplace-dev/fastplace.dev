"""Queue dashboard — the SAQ web UI behind a fail-closed auth guard.

The dashboard is an embedded third-party app with zero auth of its own, so
the whole mount is wrapped in one pure-ASGI guard that mirrors the
``auth`` middleware contract: anonymous browsers are redirected to login
with the intended URL parked, programmatic callers get the 401/403 JSON
envelope, and the optional gate ability is checked on every request —
including SAQ's POST retry/abort routes. The feature is OFF by default and
mounts only under the ``saq`` driver; a fake SAQ queue stands in for Redis
(no network, ever).
"""

from __future__ import annotations

import pytest

DASH_ENV = {
    "QUEUE_DASHBOARD_ENABLED": "true",
    "QUEUE_DRIVER": "saq",
}


class FakeSaqQueue:
    """The slice of the saq Queue surface saq_web touches — dict-backed."""

    name = "fastplace"

    async def info(self, jobs: bool = False, offset: int = 0, limit: int = 10):
        return {
            "name": self.name,
            "queued": [],
            "active": [],
            "scheduled": [] if not jobs else [{"key": "job-1", "status": "scheduled"}],
        }

    async def job(self, key: str):
        class _Job:
            async def retry(self, message: str) -> None:
                self.retried = message

            async def abort(self, message: str) -> None:
                self.aborted = message

        return _Job()


class _User:
    id = 1
    name = "Ops"


def _build(monkeypatch, **env: str):
    """Mount the dashboard on a bare Starlette host under the given env."""
    from fastplace.http.dashboard import build_dashboard_app, dashboard_path

    for key in (
        "QUEUE_DASHBOARD_ENABLED",
        "QUEUE_DASHBOARD_PATH",
        "QUEUE_DASHBOARD_ABILITY",
        "QUEUE_DRIVER",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("QUEUE_DRIVER", "saq")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    dashboard = build_dashboard_app(saq_queue=FakeSaqQueue())
    from starlette.applications import Starlette
    from starlette.routing import Mount

    routes = [Mount(dashboard_path(), app=dashboard)] if dashboard is not None else []
    return Starlette(routes=routes)


class _ScopeInject:
    """Stand-in for the global middleware stack: user + session into scope."""

    def __init__(self, app, user=..., session=None) -> None:
        self.app = app
        self.user = user
        self.session = session

    async def __call__(self, scope, receive, send) -> None:
        if self.user is not ...:
            scope["fastplace_user"] = self.user
        scope.setdefault("session", self.session if self.session is not None else {})
        await self.app(scope, receive, send)


async def _call(app, *, method="GET", path="/", root_path="", headers=None, user=..., session=None):
    """Raw ASGI call with a hand-built scope — no HTTP stack in the way."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": root_path,
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    if user is not ...:
        scope["fastplace_user"] = user
    scope["session"] = session if session is not None else {}
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start, body, scope


def _status(start) -> int:
    return start["status"]


def _header(start, name: str) -> str:
    return next(
        (v.decode() for k, v in start["headers"] if k.decode().lower() == name),
        "",
    )


# -- mounting gates ----------------------------------------------------------------


def test_dashboard_disabled_by_default_mounts_nothing(monkeypatch):
    host = _build(monkeypatch, QUEUE_DASHBOARD_ENABLED="false")
    from starlette.testclient import TestClient

    response = TestClient(host).get("/queue-dashboard/")
    assert response.status_code == 404


def test_dashboard_memory_driver_never_mounts(monkeypatch):
    """Flag on but driver memory: no mount — there is no SAQ queue to show."""
    from fastplace.http.dashboard import build_dashboard_app

    for key in (
        "QUEUE_DASHBOARD_ENABLED",
        "QUEUE_DASHBOARD_PATH",
        "QUEUE_DASHBOARD_ABILITY",
        "QUEUE_DRIVER",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("QUEUE_DASHBOARD_ENABLED", "true")
    monkeypatch.setenv("QUEUE_DRIVER", "memory")
    assert build_dashboard_app(saq_queue=FakeSaqQueue()) is None


def test_dashboard_path_is_configurable(monkeypatch):
    from starlette.testclient import TestClient

    host = _build(monkeypatch, QUEUE_DASHBOARD_PATH="/ops/queue", **DASH_ENV)
    client = TestClient(_ScopeInject(host, user=_User()))
    assert client.get("/ops/queue/").status_code == 200
    assert client.get("/queue-dashboard/").status_code == 404


# -- anonymous callers ---------------------------------------------------------------


async def test_anonymous_browser_redirects_to_login_with_intended(monkeypatch):
    host = _build(monkeypatch, **DASH_ENV)
    start, _body, scope = await _call(
        host.routes[0].app, path="/", root_path="/queue-dashboard", headers={"Accept": "text/html"}
    )

    assert _status(start) == 302
    assert _header(start, "location") == "/login"
    # The intended URL is parked for post-login resume, exactly like `auth`.
    from fastplace.auth.middleware import INTENDED_SESSION_KEY

    assert scope["session"][INTENDED_SESSION_KEY] == "/queue-dashboard/"


async def test_anonymous_xhr_gets_401_envelope(monkeypatch):
    host = _build(monkeypatch, **DASH_ENV)
    start, body, _scope = await _call(
        host.routes[0].app,
        path="/",
        root_path="/queue-dashboard",
        headers={"X-Fastplace-Request": "true"},
    )

    assert _status(start) == 401
    assert "application/json" in _header(start, "content-type")
    assert b"Unauthenticated" in body


async def test_anonymous_json_accept_gets_401_envelope(monkeypatch):
    """SAQ's own fetches ask for JSON — they get the envelope, never HTML."""
    host = _build(monkeypatch, **DASH_ENV)
    start, _body, _scope = await _call(
        host.routes[0].app,
        path="/",
        root_path="/queue-dashboard",
        headers={"Accept": "application/json"},
    )
    assert _status(start) == 401


async def test_two_factor_challenge_redirects_to_challenge(monkeypatch):
    from fastplace.auth.guards import TWO_FACTOR_CHALLENGE_KEY

    host = _build(monkeypatch, **DASH_ENV)
    start, _body, _scope = await _call(
        host.routes[0].app,
        path="/",
        root_path="/queue-dashboard",
        headers={"Accept": "text/html"},
        session={TWO_FACTOR_CHALLENGE_KEY: {"user_id": 1}},
    )
    assert _status(start) == 302
    assert _header(start, "location") == "/two-factor-challenge"


# -- authenticated callers -------------------------------------------------------------


def test_ability_none_admits_any_authenticated_user(monkeypatch):
    from starlette.testclient import TestClient

    host = _build(monkeypatch, QUEUE_DASHBOARD_ABILITY="", **DASH_ENV)
    response = TestClient(_ScopeInject(host, user=_User())).get("/queue-dashboard/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_authorized_ability_sees_the_ui(monkeypatch):
    from fastplace.authz import gate

    @gate.define("view-queue-dashboard")
    async def _allow(user):
        return True

    try:
        from starlette.testclient import TestClient

        host = _build(monkeypatch, QUEUE_DASHBOARD_ABILITY="view-queue-dashboard", **DASH_ENV)
        response = TestClient(_ScopeInject(host, user=_User())).get("/queue-dashboard/")
        assert response.status_code == 200
        assert "<html" in response.text.lower()
    finally:
        gate.reset()


def test_authenticated_without_ability_is_forbidden(monkeypatch):
    from fastplace.authz import gate

    @gate.define("view-queue-dashboard")
    async def _deny(user):
        return False

    try:
        from starlette.testclient import TestClient

        host = _build(monkeypatch, QUEUE_DASHBOARD_ABILITY="view-queue-dashboard", **DASH_ENV)
        response = TestClient(_ScopeInject(host, user=_User())).get("/queue-dashboard/")
        assert response.status_code == 403
    finally:
        gate.reset()


async def test_undefined_ability_fails_loud(monkeypatch):
    """An ability nobody defined is a configuration defect, not a silent allow."""
    from fastplace.authz import gate

    gate.reset()
    host = _build(monkeypatch, QUEUE_DASHBOARD_ABILITY="no-such-ability", **DASH_ENV)
    with pytest.raises(Exception, match="no-such-ability"):
        await _call(host.routes[0].app, path="/", root_path="/queue-dashboard", user=_User())


async def test_saq_post_route_inherits_the_guard(monkeypatch):
    """Retry/abort POSTs are guarded by the same wrapper — anonymous never lands."""
    host = _build(monkeypatch, **DASH_ENV)
    start, _body, _scope = await _call(
        host.routes[0].app,
        method="POST",
        path="/api/queues/fastplace/jobs/job-1/retry",
        root_path="/queue-dashboard",
        headers={"X-Fastplace-Request": "true"},
    )
    assert _status(start) == 401


def test_authorized_post_reaches_the_queue(monkeypatch):
    from starlette.testclient import TestClient

    from fastplace.authz import gate

    @gate.define("view-queue-dashboard")
    async def _allow(user):
        return True

    try:
        host = _build(monkeypatch, QUEUE_DASHBOARD_ABILITY="view-queue-dashboard", **DASH_ENV)
        response = TestClient(_ScopeInject(host, user=_User())).post(
            "/queue-dashboard/api/queues/fastplace/jobs/job-1/retry"
        )
        assert response.status_code == 200
    finally:
        gate.reset()
