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
        # configured policy in the frontend dialect. The two-factor props
        # drive the ManageTwoFactor card (R9).
        from fastplace.config import config

        user = getattr(request, "user", None)
        return render(
            request,
            component="Settings/Security",
            props={
                "passwordRules": frontend_rules(),
                "canManageTwoFactor": bool(config("TWO_FACTOR_ENABLED", default=True)),
                "requiresConfirmation": True,
                "twoFactorEnabled": getattr(user, "two_factor_confirmed_at", None) is not None,
            },
        )
