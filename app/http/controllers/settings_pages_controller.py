"""Account settings page controller — profile and security bridge pages.

GET-only page routes: the settings section nav already links to both pages,
and each renders fully client-side, reading optional props with graceful
degradation. The account backend itself (profile updates, password changes,
passkeys, two-factor) is Phase 4 — the form targets stay unrouted until then.
"""

from __future__ import annotations

from fastplace.http import Controller, Request, render

# The security page has no client-side default for passwordRules (the
# register page applies one), so the server supplies the same default until
# a configuration surface for password policy lands.
_PASSWORD_RULES = "minlength: 8;"


class SettingsPagesController(Controller):
    async def profile(self, request: Request):
        # Reads auth?.user as optional — renders empty fields for guests.
        return render(request, component="Settings/Profile", props={})

    async def security(self, request: Request):
        # canManagePasskeys/canManageTwoFactor stay unset; both sections
        # render their gated-off states until the auth phase lands.
        return render(
            request,
            component="Settings/Security",
            props={"passwordRules": _PASSWORD_RULES},
        )
