"""WebSocket wrapper — router.websocket() through the FastAPI/Starlette ASGI path.

The wrapper over FastAPI/Starlette primitives (blueprint §6) must actually
carry a connection end to end: accept, text/json/binary frames both ways,
and the escape hatch to the underlying Starlette WebSocket.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fastplace.http import Request, Router, get_app
from fastplace.http.websocket import WebSocket


def _echo_app() -> TestClient:
    async def echo(socket: WebSocket):
        await socket.accept()
        while True:
            try:
                text = await socket.receive_text()
            except Exception:  # noqa: BLE001 — disconnect ends the loop
                break
            if text == "close":
                await socket.close(code=1000)
                break
            await socket.send_text(f"echo:{text}")

    async def json_probe(socket: WebSocket):
        await socket.accept()
        payload = await socket.receive_json()
        await socket.send_json({"received": payload})

    async def binary_probe(socket: WebSocket):
        await socket.accept()
        data = await socket.receive_bytes()
        await socket.send_bytes(data[::-1])  # reversed echo

    async def escape_hatch(socket: WebSocket):
        await socket.accept()
        # The escape hatch exposes the Starlette websocket itself.
        await socket.starlette.send_text(f"type={type(socket.starlette).__name__}")

    router = Router()
    router.websocket("/ws/echo", echo)
    router.websocket("/ws/json", json_probe)
    router.websocket("/ws/binary", binary_probe)
    router.websocket("/ws/hatch", escape_hatch)

    app = get_app(routes=router)
    return TestClient(app)


def test_text_roundtrip_through_the_full_asgi_stack():
    with _echo_app() as client:
        with client.websocket_connect("/ws/echo") as ws:
            ws.send_text("hello")
            assert ws.receive_text() == "echo:hello"
            ws.send_text("close")


def test_json_roundtrip():
    with _echo_app() as client:
        with client.websocket_connect("/ws/json") as ws:
            ws.send_json({"a": 1})
            assert ws.receive_json() == {"received": {"a": 1}}


def test_binary_roundtrip():
    with _echo_app() as client:
        with client.websocket_connect("/ws/binary") as ws:
            ws.send_bytes(b"abc")
            assert ws.receive_bytes() == b"cba"


def test_starlette_escape_hatch_reaches_the_underlying_websocket():
    with _echo_app() as client:
        with client.websocket_connect("/ws/hatch") as ws:
            assert ws.receive_text() == "type=WebSocket"


def test_explicit_close_code_reaches_the_client():
    from starlette.websockets import WebSocketDisconnect

    with _echo_app() as client:
        with client.websocket_connect("/ws/echo") as ws:
            ws.send_text("close")
            # The next receive after the server-side close raises the
            # disconnect carrying the code the wrapper sent.
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws.receive_text()
            assert excinfo.value.code == 1000


def test_http_routes_still_work_alongside_websockets():
    async def ping(request: Request):
        return {"pong": True}

    router = Router()
    router.get("/ping", ping)

    async def echo(socket: WebSocket):
        await socket.accept()
        text = await socket.receive_text()
        await socket.send_text(text)

    router.websocket("/ws", echo)

    with TestClient(get_app(routes=router)) as client:
        assert client.get("/ping").json() == {"pong": True}
        with client.websocket_connect("/ws") as ws:
            ws.send_text("hi")
            assert ws.receive_text() == "hi"
