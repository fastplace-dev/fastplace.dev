"""Account settings page controller — profile and security bridge pages.

GET-only page routes behind the ``auth`` route middleware: the settings
section nav links to both pages and each renders fully client-side,
reading optional props with graceful degradation. The account backend
itself (profile updates, password changes, passkeys, two-factor) stays
in the later auth phases — those form targets are unrouted until then.
"""

from __future__ import annotations

from app.modules.accounts.services.password_policy import frontend_rules
from fastplace.http import Controller, Request, render


class SettingsPagesController(Controller):
    async def profile(self, request: Request):
        # Reads auth?.user as optional — the shared auth.user prop arrives
        # with the later auth phases.
        return render(request, component="Settings/Profile", props={})

    async def security(self, request: Request):
        # The security page has no client-side default for passwordRules
        # (the register page applies one), so the server supplies the
        # configured policy in the frontend dialect.
        return render(
            request,
            component="Settings/Security",
            props={"passwordRules": frontend_rules()},
        )
