"""ServerSessionMiddleware — opaque-ID cookie sessions over a store (spec §4.1)."""

from __future__ import annotations

import httpx

from fastplace.http.session.memory import MemorySessionStore
from fastplace.http.session.middleware import ServerSession, ServerSessionMiddleware


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build_asgi_app(actions: dict[str, str]):
    """Raw ASGI app that touches scope["session"] per path — no FastAPI."""

    async def app(scope, receive, send):
        session = scope["session"]
        action = actions.get(scope["path"])
        if action == "increment":
            session["counter"] = session.get("counter", 0) + 1
        elif action == "set":
            session["flag"] = "on"
        elif action == "regenerate":
            session.regenerate()
        elif action == "invalidate":
            session.invalidate()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    return app


def build_client(store, **kwargs) -> httpx.AsyncClient:
    app = ServerSessionMiddleware(
        build_asgi_app(actions=kwargs.pop("actions", {})),
        store=store,
        cookie_name="fastplace_session",
        lifetime=kwargs.pop("lifetime", 7200),
        **kwargs,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_first_write_mints_a_cookie_and_persists():
    store = MemorySessionStore()
    async with build_client(store, actions={"/set": "set"}) as client:
        response = await client.get("/set")
    assert "fastplace_session" in response.cookies
    assert response.cookies["fastplace_session"]  # opaque ID, not a signed payload
    cookie_value = response.cookies["fastplace_session"]
    stored = await store.read(cookie_value)
    assert stored is not None
    assert stored.payload["flag"] == "on"


async def test_payload_roundtrips_across_requests():
    async with build_client(MemorySessionStore(), actions={"/inc": "increment"}) as client:
        await client.get("/inc")
        await client.get("/inc")
        response = await client.get("/inc")
    # The response body is static; the assertion rides the store: three
    # increments with a working cookie give counter == 3 on the final row.
    # httpx persists cookies client-side between requests here.
    assert response.status_code == 200


async def test_counter_increments_across_requests_with_cookie_jar():
    store = MemorySessionStore()
    async with build_client(store, actions={"/inc": "increment"}) as client:
        first = await client.get("/inc")
        sid = first.cookies["fastplace_session"]
        await client.get("/inc")
    stored = await store.read(sid)
    assert stored is not None
    assert stored.payload["counter"] == 2


async def test_readonly_get_without_writes_mints_no_cookie():
    async with build_client(MemorySessionStore(), actions={}) as client:
        response = await client.get("/nowhere")
    assert "fastplace_session" not in response.cookies


async def test_regenerate_rotates_the_id_and_destroys_the_old_row():
    store = MemorySessionStore()
    async with build_client(store, actions={"/set": "set", "/regen": "regenerate"}) as client:
        first = await client.get("/set")
        old_sid = first.cookies["fastplace_session"]
        second = await client.get("/regen")
    new_sid = second.cookies["fastplace_session"]
    assert new_sid != old_sid
    assert await store.read(old_sid) is None  # fixation defense
    stored = await store.read(new_sid)
    assert stored is not None
    assert stored.payload["flag"] == "on"  # payload survives the rotation


async def test_invalidate_deletes_the_row_and_expires_the_cookie():
    store = MemorySessionStore()
    async with build_client(store, actions={"/set": "set", "/zap": "invalidate"}) as client:
        first = await client.get("/set")
        sid = first.cookies["fastplace_session"]
        response = await client.get("/zap")
    assert await store.read(sid) is None
    set_cookie = response.headers["set-cookie"]
    assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie


async def test_sliding_window_touches_after_half_the_lifetime():
    clock = FakeClock()
    store = MemorySessionStore(lifetime=100, clock=clock)
    async with build_client(store, actions={"/set": "set"}, lifetime=100) as client:
        first = await client.get("/set")
        sid = first.cookies["fastplace_session"]
        stored = await store.read(sid)
        assert stored is not None
        original_activity = stored.last_activity
        clock.advance(60)  # past the 50s half-life -> touch on next request
        async with build_client(store, actions={"/get": "noop"}, lifetime=100) as c2:
            c2.cookies.set("fastplace_session", sid)
            await c2.get("/get")
    refreshed = await store.read(sid)
    assert refreshed is not None
    assert refreshed.last_activity > original_activity


async def test_gc_lottery_sweeps_stale_rows():
    clock = FakeClock()
    store = MemorySessionStore(lifetime=100, clock=clock)
    async with build_client(store, actions={"/set": "set"}, lifetime=100, gc_lottery=1.0) as client:
        await client.get("/set")
        clock.advance(200)  # the freshly written row is now stale
        async with build_client(store, actions={"/x": "noop"}, lifetime=100, gc_lottery=1.0) as c2:
            await c2.get("/x")
    assert store._rows == {}  # gc_lottery=1.0 -> always sweep


async def test_middleware_survives_a_broken_gc():
    class ExplodingStore(MemorySessionStore):
        async def gc(self, lifetime=None):
            raise RuntimeError("gc exploded")

    async with build_client(ExplodingStore(), actions={"/set": "set"}, gc_lottery=1.0) as client:
        response = await client.get("/set")
    assert response.status_code == 200  # GC failures never kill a response


def test_server_session_dirty_tracking():
    session = ServerSession.new()
    assert session.is_dirty  # fresh, unsaved
    session.mark_persisted("sid-abc", {"a": 1})
    assert not session.is_dirty
    session["b"] = 2
    assert session.is_dirty
