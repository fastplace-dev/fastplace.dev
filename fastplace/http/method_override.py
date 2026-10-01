"""HTML form verb override — the backend half of the no-JS form contract.

HTML forms can only submit GET/POST, so the bridge's Form component ships a
``_method`` hidden field for put/patch/delete intents. This middleware reads
that field after CSRF has validated the real POST and rewrites the routing
verb, letting browsers drive the full REST surface without JavaScript.
"""

from __future__ import annotations

from fastplace.http.middleware import Middleware
from fastplace.http.request import Request
from fastplace.http.response import Response

#: The hidden input name the bridge Form uses (mirror of the frontend field).
METHOD_FIELD = "_method"

#: Verbs an override may produce. GET/HEAD/OPTIONS must never appear here —
#: rewriting to a safe verb would let a form post dodge the CSRF gate, and
#: anything exotic (TRACE, CONNECT) is not a form intent.
OVERRIDABLE_METHODS = frozenset({"put", "patch", "delete"})


class MethodOverrideMiddleware(Middleware):
    """Rewrite ``POST + _method=<verb>`` form posts to that verb before routing.

    Runs inside the configured middleware stack (the kernel registers it),
    so CSRF has already validated the request as the POST it genuinely is —
    an override can never smuggle a verb past that gate. JSON bodies are
    ignored on purpose: only a real form-encoded or multipart post carries
    the hidden input, and a JSON ``_method`` key is ordinary payload data.
    """

    async def handle(self, request: Request, call_next) -> Response:
        if request.method == "POST" and self._is_form_post(request):
            override = await self._override_from_form(request)
            if override is not None:
                request.scope["method"] = override.upper()
        return await call_next(request)

    @staticmethod
    def _is_form_post(request: Request) -> bool:
        content_type = (request.header("Content-Type") or "").lower()
        return "form-urlencoded" in content_type or "multipart/form-data" in content_type

    @staticmethod
    async def _override_from_form(request: Request) -> str | None:
        try:
            # form() consumes the receive stream without populating the body
            # cache — read the body first so BaseHTTPMiddleware can replay it
            # for the controller downstream (the CSRF middleware precedent).
            await request.body()
            form = await request.form()
        except Exception:
            return None  # unparseable body routes as the POST it arrived as
        value = form.get(METHOD_FIELD)
        if not isinstance(value, str):
            return None
        value = value.strip().lower()
        return value if value in OVERRIDABLE_METHODS else None
