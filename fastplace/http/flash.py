"""One-shot flash messages — the ``status`` channel of page payloads."""

from __future__ import annotations

from fastplace.http.request import Request

#: Session key holding the pending flash message. ServerSession dirty
#: detection is SHALLOW: only whole-value top-level assignment persists, so
#: flash() assigns the key outright and never mutates nested state.
FLASH_SESSION_KEY = "_flash"


def flash(request: Request, message: str) -> None:
    """Queue a one-shot status message on the session (tolerates no session)."""
    try:
        session = request.session
    except Exception:
        return
    if isinstance(session, dict):
        session[FLASH_SESSION_KEY] = message
