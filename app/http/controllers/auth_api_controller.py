"""Credential endpoints — thin controllers, all logic in the services."""

from __future__ import annotations

from app.http.requests.login_request import LoginRequest
from app.http.requests.register_request import RegisterRequest
from app.modules.accounts.services.auth_service import AuthService
from app.modules.accounts.services.registration_service import RegistrationService
from fastplace.http import Controller, Redirect, Request


class AuthApiController(Controller):
    auth_service = AuthService()
    registration_service = RegistrationService()

    async def login(self, request: Request):
        data = (await request.validate(LoginRequest)).model_dump()
        await self.auth_service.login(request, data)
        return Redirect(request.intended(), status_code=303)

    async def register(self, request: Request):
        data = (await request.validate(RegisterRequest)).model_dump()
        await self.registration_service.register(request, data)
        return Redirect(request.intended(), status_code=303)

    async def logout(self, request: Request):
        await self.auth_service.logout(request)
        return Redirect("/login", status_code=303)
