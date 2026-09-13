"""Fastplace WebSocket — first-class wrapper over FastAPI/Starlette primitives."""

from __future__ import annotations

from typing import Any

from starlette.websockets import WebSocket as StarletteWebSocket


class WebSocket:
    """Framework WebSocket handed to ``router.websocket(...)`` handlers."""

    def __init__(self, starlette_websocket: Any) -> None:
        self._ws = starlette_websocket

    @property
    def starlette(self) -> Any:
        """Escape hatch to the underlying Starlette WebSocket."""
        return self._ws

    async def accept(self, subprotocol: str | None = None) -> None:
        await self._ws.accept(subprotocol=subprotocol)

    async def close(self, code: int = 1000) -> None:
        await self._ws.close(code=code)

    async def send_text(self, data: str) -> None:
        await self._ws.send_text(data)

    async def send_json(self, data: Any) -> None:
        await self._ws.send_json(data)

    async def send_bytes(self, data: bytes) -> None:
        await self._ws.send_bytes(data)

    async def receive_text(self) -> str:
        return await self._ws.receive_text()

    async def receive_json(self) -> Any:
        return await self._ws.receive_json()

    async def receive_bytes(self) -> bytes:
        return await self._ws.receive_bytes()


def websocket_adapter(handler: Any) -> Any:
    """Adapt a Fastplace WebSocket handler into a Starlette websocket endpoint."""

    async def endpoint(starlette_websocket: StarletteWebSocket) -> None:
        ws = WebSocket(starlette_websocket)
        await handler(ws)

    endpoint.__name__ = getattr(handler, "__name__", "websocket_endpoint")
    return endpoint
