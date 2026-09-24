"""PAT endpoints — thin controllers, all logic in the service (spec §4.14)."""

from __future__ import annotations

from app.http.requests.mobile_token_request import MobileTokenRequest
from app.http.requests.store_token_request import StoreTokenRequest
from app.modules.accounts.services.personal_access_token_service import (
    PersonalAccessTokenService,
)
from fastplace.http import Controller, Json, Request


class PersonalAccessTokenApiController(Controller):
    service = PersonalAccessTokenService()

    async def store(self, request: Request):
        data = (await request.validate(StoreTokenRequest)).model_dump()
        issued = await self.service.issue(request, data)
        return Json(issued, status_code=201)

    async def destroy(self, request: Request):
        await self.service.revoke(request, request.param("id"))
        return Json({"ok": True})

    async def issue_mobile(self, request: Request):
        data = (await request.validate(MobileTokenRequest)).model_dump()
        issued = await self.service.issue_mobile(request, data)
        return Json(issued, status_code=201)
