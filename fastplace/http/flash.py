"""One-shot flash messages — the ``status`` channel of page payloads."""

from __future__ import annotations

from fastplace.http.request import Request

#: Session key holding the pending flash message. ServerSession dirty
#: detection is SHALLOW: only whole-value top-level assignment persists, so
#: flash() assigns the key outright and never mutates nested state.
FLASH_SESSION_KEY = "_flash"

#: Session key holding flashed validation errors. Same shallow-dirty caveat
#: as FLASH_SESSION_KEY: assign the whole value, never mutate nested state.
ERRORS_FLASH_KEY = "_errors"


def flash(request: Request, message: str) -> None:
    """Queue a one-shot status message on the session (tolerates no session)."""
    try:
        session = request.session
    except Exception:
        return
    if isinstance(session, dict):
        session[FLASH_SESSION_KEY] = message


def flash_errors(request: Request, errors: dict[str, list[str]]) -> None:
    """Queue field errors for the redirect-back re-render (tolerates no session)."""
    try:
        session = request.session
    except Exception:
        return
    if isinstance(session, dict):
        session[ERRORS_FLASH_KEY] = errors


def pop_flashed_errors(request: Request) -> dict[str, list[str]] | None:
    """Consume the flashed errors once — None when nothing is pending."""
    try:
        session = request.session
    except Exception:
        return None
    if isinstance(session, dict):
        return session.pop(ERRORS_FLASH_KEY, None)
    return None
