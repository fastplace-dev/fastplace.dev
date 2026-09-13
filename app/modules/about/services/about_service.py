"""About service — bridge props for the about page."""

from __future__ import annotations

from typing import Any


class AboutService:
    async def compose(self, *, url: str) -> dict[str, Any]:
        return {"url": url, "framework": "fastplace"}
