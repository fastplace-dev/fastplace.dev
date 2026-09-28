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
        return render(
            request,
            component="Settings/Profile",
            props={},
            title="Profile",
            robots="noindex",
        )

    async def security(self, request: Request):
        # The security page has no client-side default for passwordRules
        # (the register page applies one), so the server supplies the
        # configured policy in the frontend dialect. The two-factor props
        # drive the ManageTwoFactor card (R9). The passkey props always ship
        # so the mounted card sees a stable contract; the framework routes
        # /user/passkeys* and /passkeys/* when AUTH_PASSKEYS is enabled.
        from fastplace.config import config

        user = getattr(request, "user", None)
        passkeys_enabled = bool((config("AUTH_PASSKEYS", default={}) or {}).get("enabled", False))
        props = {
            "passwordRules": frontend_rules(),
            "canManageTwoFactor": bool(config("TWO_FACTOR_ENABLED", default=True)),
            "requiresConfirmation": True,
            "twoFactorEnabled": getattr(user, "two_factor_confirmed_at", None) is not None,
            "canManagePasskeys": passkeys_enabled,
            "passkeys": [],
        }
        if passkeys_enabled and user is not None:
            from fastplace.auth.passkey_guard import passkey_guard

            props["passkeys"] = await passkey_guard().list_for(user)
        return render(
            request,
            component="Settings/Security",
            props=props,
            title="Security",
            robots="noindex",
        )
