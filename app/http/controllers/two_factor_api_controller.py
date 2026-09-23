"""Two-factor management endpoints — thin, JSON-only (spec §4.13)."""

from __future__ import annotations

import json

from app.modules.accounts.services.two_factor_service import TwoFactorService
from fastplace.http import Controller, Json, Request


class TwoFactorApiController(Controller):
    two_factor_service = TwoFactorService()

    async def enable(self, request: Request):
        await self.two_factor_service.enable(request)
        return Json({"ok": True})

    async def confirm(self, request: Request):
        # The frozen page posts {code} — a raw JSON read matches (the
        # challenge endpoint's precedent). An undecodable or non-dict body
        # flows through as no code, and the service owns the verdict: R10's
        # flag check runs before it, then any absent/invalid code gets the
        # one frozen 422.
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        if not isinstance(body, dict):
            body = {}
        code = str(body.get("code") or "").strip()
        await self.two_factor_service.confirm(request, code)
        return Json({"ok": True})

    async def disable(self, request: Request):
        await self.two_factor_service.disable(request)
        return Json({"ok": True})

    async def regenerate_recovery_codes(self, request: Request):
        return Json(await self.two_factor_service.regenerate_recovery_codes(request))

    async def recovery_codes(self, request: Request):
        return Json(await self.two_factor_service.recovery_codes(request))

    async def qr_code(self, request: Request):
        return Json(await self.two_factor_service.qr_code_payload(request))

    async def secret_key(self, request: Request):
        return Json(await self.two_factor_service.secret_key_payload(request))
