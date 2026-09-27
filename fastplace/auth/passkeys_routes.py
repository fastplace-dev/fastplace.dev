"""Framework-owned passkey routes — mounted by create_app, zero app code.

The scaffold frontend's existing components define this contract verbatim;
the backend implements it. When the ``webauthn`` extra is missing the
handlers answer 501 with the install message instead of breaking boot.
"""

from __future__ import annotations

from typing import Any

from fastplace.errors import ValidationError
from fastplace.http.flash import flash
from fastplace.http.redirect_back import safe_back_url, wants_redirect_back
from fastplace.http.response import Json, Redirect
from fastplace.http.router import Router

MISSING_EXTRA_MESSAGE = (
    "Passkey support requires the 'webauthn' extra: pip install 'fastplace[webauthn]'"
)

PASSKEY_ADDED = "Passkey added."
PASSKEY_REMOVED = "Passkey removed."
IDENTITY_VERIFIED = "Identity verified."

TWO_FACTOR_CHALLENGE_REDIRECT = "/two-factor-challenge"


def _missing_extra_response() -> Json:
    return Json({"message": MISSING_EXTRA_MESSAGE}, status_code=501)


def _passkeys_enabled() -> bool:
    """AUTH_PASSKEYS.enabled, with the env var winning (framework convention).

    An app that flips AUTH_PASSKEYS.enabled off gets no passkey routes at
    all; the scaffold default (enabled) mounts them unconditionally.
    """
    from fastplace.config import config

    block = config("AUTH_PASSKEYS", default=None)
    if not isinstance(block, dict):
        block = {}
    raw = config("APP_PASSKEYS_ENABLED", default=None)
    if raw is None or raw == "":
        raw = block.get("enabled", False)
    if isinstance(raw, str):
        return raw.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(raw)


def build_passkey_router() -> Router:
    class PasskeysController:
        async def options(self, request: Any):
            if not _available():
                return _missing_extra_response()
            from fastplace.auth.passkey_guard import passkey_guard

            return await passkey_guard().registration_options(request, request.user)

        async def store(self, request: Any):
            if not _available():
                return _missing_extra_response()
            from fastplace.auth.passkey_guard import passkey_guard

            body = await _body(request)
            name = str((body or {}).get("name") or "").strip()
            credential = (body or {}).get("credential")
            if not name:
                raise ValidationError(errors={"name": ["The name field is required."]})
            if not isinstance(credential, dict):
                raise ValidationError(errors={"credential": ["The credential field is required."]})
            await passkey_guard().register(request, request.user, name, credential)
            return await _success(request, PASSKEY_ADDED)

        async def destroy(self, request: Any):
            if not _available():
                return _missing_extra_response()
            from fastplace.auth.passkey_guard import passkey_guard

            credential_id = _credential_id(request)
            removed = await passkey_guard().delete(request, request.user, credential_id)
            if not removed:
                raise ValidationError(errors={"credential": ["That passkey was not found."]})
            return await _success(request, PASSKEY_REMOVED)

        async def login_options(self, request: Any):
            if not _available():
                return _missing_extra_response()
            from fastplace.auth.passkey_guard import passkey_guard

            return await passkey_guard().login_options(request)

        async def login(self, request: Any):
            if not _available():
                return _missing_extra_response()
            from fastplace.auth.passkey_guard import passkey_guard

            body = await _body(request)
            credential = (body or {}).get("credential")
            if not isinstance(credential, dict):
                raise ValidationError(errors={"credential": ["The credential field is required."]})
            user = await passkey_guard().login(request, credential)
            if user is None:  # the 2FA challenge was parked — passkey replaced the password only
                return {"redirect": TWO_FACTOR_CHALLENGE_REDIRECT}
            return {"redirect": request.intended()}

        async def confirm_options(self, request: Any):
            if not _available():
                return _missing_extra_response()
            from fastplace.auth.passkey_guard import passkey_guard

            return await passkey_guard().confirm_options(request, request.user)

        async def confirm(self, request: Any):
            if not _available():
                return _missing_extra_response()
            from fastplace.auth.passkey_guard import passkey_guard

            body = await _body(request)
            credential = (body or {}).get("credential")
            if not isinstance(credential, dict):
                raise ValidationError(errors={"credential": ["The credential field is required."]})
            await passkey_guard().confirm(request, request.user, credential)
            if wants_redirect_back(request):
                flash(request, IDENTITY_VERIFIED)
                return Redirect(request.intended(), status_code=303)
            return {"redirect": request.intended()}

    # Module-attribute lookups (not from-imports) so tests can monkeypatch
    # availability and the guard accessor per call.
    def _available() -> bool:
        from fastplace.auth import webauthn as webauthn_ceremony

        return webauthn_ceremony.webauthn_available()

    async def _body(request: Any) -> dict[str, Any] | None:
        content_type = (request.header("Content-Type") or "").lower()
        if "json" in content_type:
            return await request.json()
        return await request.form()

    def _credential_id(request: Any) -> int:
        raw = request.param("id")
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValidationError(errors={"credential": ["That passkey was not found."]}) from None

    async def _success(request: Any, message: str):
        if wants_redirect_back(request):
            flash(request, message)
            return Redirect(safe_back_url(request), status_code=303)
        return {"ok": True}

    router = Router()
    router.get(
        "/user/passkeys/options",
        PasskeysController,
        "options",
        name="passkeys.options",
        middleware=["auth", "verified"],
    )
    router.post(
        "/user/passkeys",
        PasskeysController,
        "store",
        name="passkeys.store",
        middleware=["auth", "verified"],
    )
    router.delete(
        "/user/passkeys/{id}",
        PasskeysController,
        "destroy",
        name="passkeys.destroy",
        middleware=["auth", "verified"],
    )
    router.get(
        "/passkeys/login/options",
        PasskeysController,
        "login_options",
        name="passkeys.login.options",
        middleware=["guest"],
    )
    router.post(
        "/passkeys/login",
        PasskeysController,
        "login",
        name="passkeys.login",
        middleware=["guest"],
    )
    router.get(
        "/passkeys/confirm/options",
        PasskeysController,
        "confirm_options",
        name="passkeys.confirm.options",
        middleware=["auth"],
    )
    router.post(
        "/passkeys/confirm",
        PasskeysController,
        "confirm",
        name="passkeys.confirm",
        middleware=["auth"],
    )
    return router


def mount_passkey_routes(auth: Router | None) -> Router | None:
    """Merge the framework passkey routes into the app's auth router.

    App routes keep first-match priority; the passkey paths are unique, so
    no conflict exists. The enabled check lives here (config/auth.py's
    AUTH_PASSKEYS block) — an app that flips it off gets no routes at all.
    """
    if not _passkeys_enabled():
        return auth
    extra = build_passkey_router()
    auth = auth if auth is not None else Router()
    auth.routes.extend(extra.routes)
    return auth
