"""English lines — the one locale the starter ships.

``app/support/lang.py`` reads this module; add more locales as siblings
(``lang/<locale>/messages.py``) and they resolve through the same helper.
"""

LINES = {
    "auth.failed": "These credentials do not match our records.",
    "auth.throttled": "Too many attempts. Please try again in :seconds seconds.",
    "verification.sent": "A fresh verification link has been sent to your email.",
    "passwords.reset": "Your password has been reset.",
    "passwords.token": "This password reset token is invalid.",
    "passwords.user": "We can't find a user with that email address.",
    "auth.passkey.verify_failed": "Unable to verify this passkey.",
    "auth.passkey.added": "Passkey added.",
    "auth.passkey.removed": "Passkey removed.",
    "auth.passkey.confirmed": "Identity verified.",
    "auth.passkey.already_registered": "That passkey is already registered.",
}
