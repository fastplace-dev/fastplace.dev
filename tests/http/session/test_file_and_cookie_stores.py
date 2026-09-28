"""File + encrypted-cookie session stores (sweep-G9).

The file store keeps one JSON file per session under
``storage/framework/sessions``; the cookie store encrypts the whole payload
into the cookie value itself (stateless — nothing lives server-side).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from fastplace.errors import ConfigurationError

VALID_ID = "a" * 64  # token_hex(32) shape — the only id shape stores accept


@pytest.fixture()
def clock():
    ticks = {"now": 1_700_000_000.0}

    def get() -> float:
        return ticks["now"]

    def advance(seconds: float) -> None:
        ticks["now"] += seconds

    return get, advance


# -- file store -----------------------------------------------------------


@pytest.fixture()
def file_store(clock, tmp_path):
    from fastplace.http.session.file_store import FileSessionStore

    get, advance = clock
    return FileSessionStore(root=tmp_path / "sessions", clock=get)


async def test_file_store_round_trips_payload_to_a_json_file(file_store, tmp_path):
    await file_store.write(VALID_ID, {"user_id": 7, "cart": ["x"]}, user_id=7)

    stored = await file_store.read(VALID_ID)
    assert stored is not None
    assert stored.payload == {"user_id": 7, "cart": ["x"]}
    assert stored.last_activity == int(1_700_000_000.0)
    # One file per session, JSON on disk under the root.
    files = list((tmp_path / "sessions").iterdir())
    assert [f.name for f in files] == [f"{VALID_ID}.json"]
    on_disk = json.loads(files[0].read_text())
    assert on_disk["payload"]["user_id"] == 7


async def test_file_store_lazy_expiry_after_lifetime(file_store, clock):
    get, advance = clock
    await file_store.write(VALID_ID, {"user_id": 1})
    advance(3600)
    assert await file_store.read(VALID_ID) is not None  # inside the window
    advance(7200)  # default SESSION_LIFETIME
    assert await file_store.read(VALID_ID) is None  # expired rows read as missing


async def test_file_store_destroy_and_destroy_for_user(file_store):
    await file_store.write("b" * 64, {"user_id": 5}, user_id=5)
    await file_store.write("c" * 64, {"user_id": 5}, user_id=5)
    await file_store.write("d" * 64, {"user_id": 6}, user_id=6)

    await file_store.destroy("b" * 64)
    assert await file_store.read("b" * 64) is None

    destroyed = await file_store.destroy_for_user(5, except_session_id="d" * 64)
    assert destroyed == 1  # only the other user-5 row
    assert await file_store.read("c" * 64) is None
    assert await file_store.read("d" * 64) is not None


async def test_file_store_gc_sweeps_only_stale_rows(file_store, clock):
    get, advance = clock
    await file_store.write("e" * 64, {"user_id": 1})
    advance(10_000)
    await file_store.write("f" * 64, {"user_id": 1})

    removed = await file_store.gc(lifetime=7200)
    assert removed == 1
    assert await file_store.read("e" * 64) is None
    assert await file_store.read("f" * 64) is not None


def _spy_loads(store, parsed: list[str]):
    real = store._safe_load

    def spy(path):
        parsed.append(Path(path).name)
        return real(path)

    return spy


async def test_gc_prefilter_never_parses_in_window_files(tmp_path, monkeypatch):
    # The sweep's stat prefilter is armed on the real wall clock (an
    # injected fake clock outruns mtimes, so there the full scan runs) —
    # in-window files must be skipped by stat alone, never read+parsed.
    from fastplace.http.session.file_store import FileSessionStore

    store = FileSessionStore(root=tmp_path / "sessions")
    await store.write("a" * 64, {"user_id": 1})
    await store.write("b" * 64, {"user_id": 1})
    back = time.time() - 99_999  # far past the default window
    path_b = tmp_path / "sessions" / f"{'b' * 64}.json"
    record = json.loads(path_b.read_text())
    record["last_activity"] = int(back)  # genuinely stale, not just an old mtime
    path_b.write_text(json.dumps(record))
    os.utime(path_b, (back, back))

    parsed: list[str] = []
    monkeypatch.setattr(store, "_safe_load", _spy_loads(store, parsed))

    removed = await store.gc(lifetime=7200)

    assert removed == 1
    assert await store.read("a" * 64) is not None
    assert parsed == [f"{'b' * 64}.json"]  # the fresh file was never read


async def test_gc_sweeps_corrupt_files_that_can_never_expire(tmp_path):
    # A torn/unparseable file carries no expirable last_activity — gc must
    # collect it instead of hoarding it forever.
    from fastplace.http.session.file_store import FileSessionStore

    root = tmp_path / "sessions"
    store = FileSessionStore(root=root)
    torn = root / f"{'c' * 64}.json"
    torn.write_text("{torn")  # invalid JSON under a valid session-id name
    back = time.time() - 99_999
    os.utime(torn, (back, back))

    removed = await store.gc(lifetime=7200)

    assert removed == 0  # not a stale SESSION — but the fossil is gone
    assert not torn.exists()


async def test_gc_sweeps_stale_tmp_orphans_but_leaves_fresh_ones(tmp_path):
    # A worker killed between the tmp write and the replace leaves a
    # plaintext payload dot-file no *.json sweep would ever see. gc must
    # unlink them past a grace window — never a live writer's fresh tmp.
    from fastplace.http.session.file_store import FileSessionStore

    root = tmp_path / "sessions"
    store = FileSessionStore(root=root)
    orphan = root / f".{'a' * 64}.{os.getpid()}.deadbeefcafe.tmp"
    orphan.write_text('{"payload": {"user_id": 1}}')
    fresh = root / f".{'b' * 64}.{os.getpid()}.feedfacebeef.tmp"
    fresh.write_text("{}")
    back = time.time() - 3600
    os.utime(orphan, (back, back))

    await store.gc(lifetime=7200)

    assert not orphan.exists()
    assert fresh.exists()


async def test_file_store_rejects_unsafe_session_ids(file_store, tmp_path):
    # A cookie value is attacker-controlled — anything that is not the minted
    # hex shape must never touch the filesystem, not even as a lookup.
    for hostile in ("../../etc/passwd", "../sessions", "x" * 64, "", "a" * 63, "A" * 64):
        assert await file_store.read(hostile) is None
        await file_store.destroy(hostile)  # no-op, no raise
    assert list((tmp_path / "sessions").iterdir()) == []

    with pytest.raises(ValueError):
        await file_store.write("../escape", {"user_id": 1})


async def test_file_store_sessions_for_user_lists_active_rows_newest_first(file_store, clock):
    get, advance = clock
    await file_store.write("1" * 64, {"user_id": 9}, user_id=9)
    advance(100)
    await file_store.write("2" * 64, {"user_id": 9}, user_id=9)
    advance(100)
    await file_store.write("3" * 64, {"user_id": 9}, user_id=9)

    rows = await file_store.sessions_for_user(9)
    assert [row.id for row in rows] == ["3" * 64, "2" * 64, "1" * 64]

    advance(7300)  # every row is past the window now
    assert await file_store.sessions_for_user(9) == []


def test_factory_builds_file_store():
    from fastplace.http.session import FileSessionStore, session_store

    store = session_store(config_get=lambda key, default=None: "file")
    assert isinstance(store, FileSessionStore)


# -- cookie store ---------------------------------------------------------


@pytest.fixture(autouse=True)
def _app_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_KEY", "k" * 64)


@pytest.fixture()
def cookie_store(clock):
    from fastplace.http.session.cookie_store import CookieSessionStore

    get, _advance = clock
    return CookieSessionStore(clock=get)


async def test_cookie_store_write_returns_an_encrypted_blob(cookie_store):
    blob = await cookie_store.write(VALID_ID, {"user_id": 3, "flash": "hi"})
    assert isinstance(blob, str) and blob
    assert "user_id" not in blob and "flash" not in blob  # ciphertext only


async def test_cookie_store_round_trips_through_the_blob(cookie_store, clock):
    get, advance = clock
    blob = await cookie_store.write(VALID_ID, {"user_id": 3})
    advance(60)
    stored = await cookie_store.read(blob)
    assert stored is not None
    assert stored.payload == {"user_id": 3}
    assert stored.last_activity == int(1_700_000_000.0)


async def test_cookie_store_tampered_blob_reads_as_missing(cookie_store):
    blob = await cookie_store.write(VALID_ID, {"user_id": 3})
    assert await cookie_store.read(blob[:-4] + "AAAA") is None
    assert await cookie_store.read("not-a-blob") is None


async def test_cookie_store_lazy_expiry(cookie_store, clock):
    get, advance = clock
    blob = await cookie_store.write(VALID_ID, {"user_id": 3})
    advance(7300)
    assert await cookie_store.read(blob) is None  # past SESSION_LIFETIME


async def test_cookie_store_stateless_ops_are_noops(cookie_store):
    blob = await cookie_store.write(VALID_ID, {"user_id": 3})
    await cookie_store.destroy(blob)  # no raise
    assert await cookie_store.destroy_for_user(3) == 0
    assert await cookie_store.gc() == 0


async def test_cookie_store_refuses_oversized_payloads(cookie_store):
    with pytest.raises(ConfigurationError):
        await cookie_store.write(VALID_ID, {"blob": "x" * 8000})


async def test_cookie_sessions_do_not_share_the_two_factor_key_domain(
    monkeypatch: pytest.MonkeyPatch,
):
    """Domain separation: a session blob must not decrypt as 2FA material and
    vice versa — same APP_KEY, different HKDF info strings."""
    from fastplace.auth.encryption import decrypt, encrypt
    from fastplace.http.session.cookie_store import COOKIE_HKDF_INFO, CookieSessionStore

    store = CookieSessionStore()
    blob = await store.write(VALID_ID, {"user_id": 3})
    with pytest.raises(ValueError):
        decrypt(blob)  # default info is the two_factor domain
    # A token minted in the 2FA domain is not a session — it reads as missing.
    assert await store.read(encrypt("two-factor-secret")) is None
    # The session domain itself still round-trips.
    assert await store.read(blob) is not None
    assert COOKIE_HKDF_INFO != "two_factor"


def test_factory_builds_cookie_store():
    from fastplace.http.session import CookieSessionStore, session_store

    store = session_store(config_get=lambda key, default=None: "cookie")
    assert isinstance(store, CookieSessionStore)


async def test_cookie_driver_runs_end_to_end_through_the_middleware():
    """Full stack: the middleware must hydrate from the encrypted cookie alone
    — a second app instance sharing only APP_KEY serves the same session."""
    import httpx

    from fastplace.http.kernel import get_app
    from fastplace.http.router import Router

    async def handler(request):
        count = request.session.get("hits", 0) + 1
        request.session["hits"] = count
        from fastplace.http.response import Json

        return Json({"hits": count})

    def build():
        router = Router()
        router.get("/count", handler)
        return get_app(
            routes=router,
            config={"APP_ENV": "local", "APP_KEY": "cookie-test-key", "SESSION_DRIVER": "cookie"},
        )

    transport = httpx.ASGITransport(app=build())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/count")
        # A fresh app process — server-side state cannot have carried over.
        transport2 = httpx.ASGITransport(app=build())
        async with httpx.AsyncClient(transport=transport2, base_url="http://test") as other:
            second = await other.get("/count", cookies=first.cookies)
    assert first.json() == {"hits": 1}
    assert second.json() == {"hits": 2}


async def test_middleware_sets_the_cookie_to_the_store_returned_value():
    """Contract extension: a stateless store's write() may return the value
    the cookie must carry — the middleware uses it verbatim."""
    from fastplace.http.session.middleware import ServerSessionMiddleware

    class EchoStore:
        async def read(self, session_id):
            return None

        async def write(self, session_id, payload, *, user_id=None):
            return f"echo:{payload.get('hits')}"

        async def destroy(self, session_id):
            return None

        async def destroy_for_user(self, user_id, *, except_session_id=None):
            return 0

        async def gc(self, lifetime=None):
            return 0

    async def app_inner(scope, receive, send):
        scope["session"]["hits"] = 5
        await send(
            {"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"0")]}
        )
        await send({"type": "http.response.body", "body": b""})

    middleware = ServerSessionMiddleware(app_inner, store=EchoStore(), lifetime=7200)
    cookies: list[str] = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        if message["type"] == "http.response.start":
            for name, value in message.get("headers", []):
                if name == b"set-cookie":
                    cookies.append(value.decode("latin-1"))

    await middleware(
        {"type": "http", "headers": [(b"cookie", b"")], "path": "/", "query_string": b""},
        receive,
        send,
    )
    assert any("echo:5" in cookie for cookie in cookies)


async def test_write_uses_a_unique_scratch_file_per_write(tmp_path, monkeypatch):
    """Two writers must never share a tmp file — a deterministic name lets a
    second writer truncate the first's in-flight scratch inode (multi-worker
    `serve` shares the directory) and the loser's replace publishes torn JSON
    or explodes FileNotFoundError mid-response."""
    import fastplace.http.session.file_store as file_store_module
    from fastplace.http.session.file_store import FileSessionStore

    store = FileSessionStore(root=tmp_path)
    seen: list[str] = []
    real_replace = file_store_module.os.replace

    def spy(src, dst):
        seen.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(file_store_module.os, "replace", spy)

    safe_id = "a" * 32
    await store.write(safe_id, {"n": 1})
    await store.write(safe_id, {"n": 2})
    assert len(seen) == 2
    assert seen[0] != seen[1], "scratch file names must be unique per write"


async def test_concurrent_writers_on_the_same_session_never_corrupt_it(tmp_path):
    """Four threads writing the same session id concurrently: every published
    file stays parseable JSON (last-writer-wins, never torn)."""
    import asyncio
    import threading

    from fastplace.http.session.file_store import FileSessionStore

    store = FileSessionStore(root=tmp_path)
    safe_id = "b" * 32
    errors: list[Exception] = []

    async def hammer(writer: int) -> None:
        for step in range(25):
            try:
                await store.write(safe_id, {"writer": writer, "step": step})
                await store.read(safe_id)
            except Exception as exc:  # noqa: BLE001 — collect, assert below
                errors.append(exc)

    def run(writer: int) -> None:
        asyncio.run(hammer(writer))

    threads = [threading.Thread(target=run, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    stored = await store.read(safe_id)
    assert stored is not None
    payload = stored.payload
    assert set(payload) == {"writer", "step"}
