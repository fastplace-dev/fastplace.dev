"""Dev entrypoint — the project app, or a boot-failure shell that answers.

``fastplace run dev`` points uvicorn ``--reload`` here instead of straight
at ``asgi:app``. When a Python error kills the project import (a syntax
error mid-edit is the classic), uvicorn's reloader keeps the listening
socket but never accepts — the browser hangs with zero feedback until the
file is fixed. Importing through this module gives the failed boot a real
responder instead: a self-refreshing 503 page that swaps back to the real
app on the next reload.
"""

from __future__ import annotations

from typing import Any

boot_error: str | None = None
boot_traceback: str = ""

try:
    from asgi import app  # type: ignore[no-redef,import-untyped]
except Exception as _exc:  # noqa: BLE001 — any project boot failure gets the shell
    import traceback as _traceback

    boot_error = f"{type(_exc).__name__}: {_exc}"
    boot_traceback = _traceback.format_exc()

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:  # type: ignore[misc]
        if scope["type"] == "lifespan":
            # Minimal lifespan: complete startup so the server adopts us.
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return
        if scope["type"] != "http":
            return
        from fastplace.http.error_pages import boot_error_page

        page = boot_error_page(boot_error or "", boot_traceback)
        await page(scope, receive, send)
