"""Credential + verification endpoints (spec §4.5/§4.11) — the GET pages live in web.py."""

from app.http.controllers.auth_api_controller import AuthApiController
from fastplace.http import Router

router = Router()

# The ".store" suffix avoids colliding with the GET routes' auth.login /
# auth.register names in routes/web.py. "guest" on the POSTs mirrors the GET
# pages — a logged-in client posting /login is bounced, not re-logged.
router.post(
    "/login",
    AuthApiController,
    "login",
    name="auth.login.store",
    middleware=["guest", "throttle:5,60"],
)
router.post(
    "/register",
    AuthApiController,
    "register",
    name="auth.register.store",
    middleware=["guest", "throttle:5,60"],
)
router.post(
    "/logout",
    AuthApiController,
    "logout",
    name="auth.logout",
    middleware=["auth"],
)
router.post(
    "/forgot-password",
    AuthApiController,
    "forgot_password",
    name="auth.forgot_password.store",
    middleware=["throttle:5,60"],  # NO guest — a logged-in user may reset too
)
router.post(
    "/reset-password",
    AuthApiController,
    "reset_password",
    name="auth.reset_password.store",
    middleware=["throttle:5,60"],
)

# Email verification — the notice page lives in web.py; these are the
# signed-link redemption and the resend (spec §4.11).
router.get(
    "/email/verify/{id}/{hash}",
    AuthApiController,
    "verify_email",
    name="auth.verify_email.fulfill",
    middleware=["auth"],
)
router.post(
    "/email/verification-notification",
    AuthApiController,
    "verification_notification",
    name="auth.verification_notification.store",
    # Spec §4.19's throttle:6,1 — fastplace's D is SECONDS (R8).
    middleware=["auth", "throttle:6,60"],
)

# Password confirmation (spec §4.12) — the confirmation window the
# password.confirm route middleware enforces.
router.post(
    "/user/confirm-password",
    AuthApiController,
    "confirm_password",
    name="auth.confirm_password.store",
    middleware=["auth", "throttle:6,60"],  # spec's 6/min — D is seconds (R8)
)
