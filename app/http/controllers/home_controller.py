"""Home controller — the framework landing page at "/".

The home page is fully client-side (it adapts to the optional shared auth
data), so there is no service layer to consult — the controller only names
the page.
"""

from __future__ import annotations

from fastplace.http import Controller, Request, render


class HomeController(Controller):
    async def index(self, request: Request):
        return render(
            request,
            component="Home/Index",
            props={},
            title="Home",
            description="Fastplace — an opinionated, full-stack, AI-native web framework "
            "built on Python and React.",
            canonical="/",
        )
