"""Settings write endpoints — profile, password, account deletion (§4.14).

The shipped settings UI posts to these three targets; the controllers
stay thin and the rules live in the request schemas and the service.
"""

from __future__ import annotations

from app.http.requests.delete_profile_request import DeleteProfileRequest
from app.http.requests.password_update_request import PasswordUpdateRequest
from app.http.requests.profile_request import ProfileRequest
from app.modules.accounts.services.settings_service import SettingsService
from fastplace.http import Controller, Redirect, Request, flash


class SettingsApiController(Controller):
    # The service seam, not the repository: controllers stay outside the
    # module boundary that lint:modules enforces.
    settings = SettingsService()

    async def index(self, request: Request):
        # The settings section has no page of its own — /settings is an alias.
        return Redirect("/settings/profile", status_code=303)

    async def update_profile(self, request: Request):
        data = (await request.validate(ProfileRequest)).model_dump()
        email_changed = await self.settings.update_profile(request.user, data)
        if email_changed:
            flash(request, "Profile updated. Please verify your new email address.")
        else:
            flash(request, "Profile updated.")
        return Redirect("/settings/profile", status_code=303)

    async def update_password(self, request: Request):
        data = (await request.validate(PasswordUpdateRequest)).model_dump()
        await self.settings.update_password(request, request.user, data)
        flash(request, "Password updated.")
        return Redirect("/settings/security", status_code=303)

    async def destroy(self, request: Request):
        data = (await request.validate(DeleteProfileRequest)).model_dump()
        await self.settings.delete_account(request, request.user, data["password"])
        return Redirect("/", status_code=303)
