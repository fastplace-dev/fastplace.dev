"""Auth page controller — the guest auth pages.

GET-only page routes: the ported React pages render fully client-side and
read their optional props with typed defaults, so no service layer is
consulted yet. The authentication backend itself (sessions, credentials,
passkeys) is Phase 4; form submits stay unrouted until then.
"""

from __future__ import annotations

from fastplace.http import Controller, Request, render


class AuthPageController(Controller):
    async def login(self, request: Request):
        return render(request, component="Auth/Login", props={})

    async def register(self, request: Request):
        return render(request, component="Auth/Register", props={})

    async def forgot_password(self, request: Request):
        return render(request, component="Auth/ForgotPassword", props={})

    async def reset_password(self, request: Request):
        # The reset link arrives as /reset-password?token=...&email=... —
        # the page reads both from props, not from the query string.
        return render(
            request,
            component="Auth/ResetPassword",
            props={
                "token": request.query("token", ""),
                "email": request.query("email", ""),
            },
        )

    async def verify_email(self, request: Request):
        return render(request, component="Auth/VerifyEmail", props={})

    async def confirm_password(self, request: Request):
        return render(request, component="Auth/ConfirmPassword", props={})

    async def two_factor_challenge(self, request: Request):
        return render(request, component="Auth/TwoFactorChallenge", props={})
