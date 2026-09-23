"""Credential endpoints (spec §4.5) — POST-only; the GET pages live in web.py."""

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
