"""Auth page controller — the guest auth pages.

GET-only page routes behind the ``guest`` route middleware: the ported
React pages render fully client-side and read their optional props with
typed defaults. The credential endpoints live in routes/auth.py; the
later auth phases (passkeys, two-factor) will wire their remaining form
targets.
"""

from __future__ import annotations

from app.modules.accounts.services.password_policy import frontend_rules
from fastplace.http import Controller, Request, render


class AuthPageController(Controller):
    async def login(self, request: Request):
        return render(request, component="Auth/Login", props={})

    async def register(self, request: Request):
        # The register page's client-side default is "minlength: 8;" — the
        # server prop mirrors the same policy that validates the POST.
        return render(
            request,
            component="Auth/Register",
            props={"passwordRules": frontend_rules()},
        )

    async def forgot_password(self, request: Request):
        return render(request, component="Auth/ForgotPassword", props={})

    async def reset_password(self, request: Request):
        # The email link lands on /reset-password/{token}?email=... — the
        # page reads token/email from props, never from the URL directly.
        return render(
            request,
            component="Auth/ResetPassword",
            props={
                "token": request.param("token"),
                "email": request.query("email", ""),
                "passwordRules": frontend_rules(),
            },
        )

    async def verify_email(self, request: Request):
        return render(request, component="Auth/VerifyEmail", props={})

    async def confirm_password(self, request: Request):
        return render(request, component="Auth/ConfirmPassword", props={})

    async def two_factor_challenge(self, request: Request):
        return render(request, component="Auth/TwoFactorChallenge", props={})
