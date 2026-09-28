"""Settings appearance controller — the appearance settings bridge page.

The appearance settings are fully client-side (persisted per viewer), so
there is no service layer to consult — the controller only names the page.
"""

from __future__ import annotations

from fastplace.http import Controller, Request, render


class SettingsAppearanceController(Controller):
    async def index(self, request: Request):
        return render(
            request,
            component="Settings/Appearance",
            props={},
            title="Appearance",
            robots="noindex",
        )
