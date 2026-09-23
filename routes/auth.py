"""Credential + verification endpoints (spec §4.5/§4.11) — the GET pages live in web.py."""

from app.http.controllers.auth_api_controller import AuthApiController
from app.http.controllers.two_factor_api_controller import TwoFactorApiController
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

# Two-factor challenge fulfillment (spec §4.13) — the login interrupt's
# second half. Guest: the parked session is anonymous by design.
router.post(
    "/two-factor-challenge",
    AuthApiController,
    "two_factor_challenge",
    name="auth.two_factor_challenge.store",
    middleware=["guest", "throttle:5,60"],
)

# Two-factor management (spec §4.13) — every route behind auth + a recent
# password confirmation (the first routes to name the alias). The confirm
# URL is the frozen frontend's /user/confirmed-two-factor-authentication
# (R1), and the GET on the recovery-codes URL serves the hook's bare-array
# fetch (R8: GET + POST share one URL).
_TWO_FACTOR_MIDDLEWARE = ["auth", "password.confirm"]
router.post(
    "/user/two-factor-authentication",
    TwoFactorApiController,
    "enable",
    name="auth.two_factor.enable",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
router.delete(
    "/user/two-factor-authentication",
    TwoFactorApiController,
    "disable",
    name="auth.two_factor.disable",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
router.post(
    "/user/confirmed-two-factor-authentication",
    TwoFactorApiController,
    "confirm",
    name="auth.two_factor.confirm",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
router.post(
    "/user/two-factor-recovery-codes",
    TwoFactorApiController,
    "regenerate_recovery_codes",
    name="auth.two_factor.regenerate",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
router.get(
    "/user/two-factor-recovery-codes",
    TwoFactorApiController,
    "recovery_codes",
    name="auth.two_factor.recovery_codes",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
router.get(
    "/user/two-factor-qr-code",
    TwoFactorApiController,
    "qr_code",
    name="auth.two_factor.qr_code",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
router.get(
    "/user/two-factor-secret-key",
    TwoFactorApiController,
    "secret_key",
    name="auth.two_factor.secret_key",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
