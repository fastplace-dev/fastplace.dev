"""Time travel — freeze the clock, then move it while frozen.

Backed by ``freezegun`` (the optional ``testing`` extra). ``travel`` moves
the frozen moment by a relative delta — TTL deadlines, retry backoff, and
"three days later" logic become deterministic without re-freezing.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from typing import Any

_CLOCK_HINT = (
    "the clock fixture needs freezegun — install it with: pip install 'fastplace[testing]'"
)

When = str | datetime.datetime | datetime.date


def _as_datetime(when: When) -> datetime.datetime:
    moment: datetime.datetime
    if isinstance(when, datetime.datetime):
        moment = when
    elif isinstance(when, datetime.date):
        moment = datetime.datetime.combine(when, datetime.time.min)
    else:
        moment = datetime.datetime.fromisoformat(when)
    if moment.tzinfo is None:
        # Unfrozen now() is always UTC-aware; a naive input must freeze to
        # the same convention, or deadline comparisons against real wall
        # time raise "can't compare offset-naive and offset-aware".
        return moment.replace(tzinfo=datetime.UTC)
    return moment


class Clock:
    """Freeze/travel/move_to handle; one live freezer at a time."""

    def __init__(self, freeze_factory: Callable[[Any], Any] | None = None) -> None:
        # The factory is injected (and freezegun imported) by the fixture so
        # a missing optional dependency raises at fixture time, not import.
        if freeze_factory is None:
            from fastplace.errors import ConfigurationError

            raise ConfigurationError(_CLOCK_HINT)
        self._freeze_factory = freeze_factory
        # freezegun separates the decorator object (start/stop) from the
        # FrozenDateTimeFactory it hands back (tick/move_to) — keep both.
        self._decorator: Any = None
        self._factory: Any = None
        self._moment: datetime.datetime | None = None

    def freeze(self, when: When) -> datetime.datetime:
        """Stop the wall clock at ``when``; returns the frozen moment."""
        self._stop()
        moment = _as_datetime(when)
        self._decorator = self._freeze_factory(moment)
        self._factory = self._decorator.start()
        self._moment = moment
        return moment

    def travel(self, **delta: Any) -> datetime.datetime:
        """Move the frozen moment forward (``days=1, hours=2`` style)."""
        if self._factory is None or self._moment is None:
            raise RuntimeError("clock.travel() needs a frozen clock — call freeze() first")
        step = datetime.timedelta(**delta)
        self._factory.tick(step)
        self._moment = self._moment + step
        return self._moment

    def move_to(self, when: When) -> datetime.datetime:
        """Jump the frozen moment to an absolute time."""
        if self._factory is None or self._moment is None:
            raise RuntimeError("clock.move_to() needs a frozen clock — call freeze() first")
        moment = _as_datetime(when)
        self._factory.move_to(moment)
        self._moment = moment
        return moment

    @property
    def frozen(self) -> bool:
        return self._decorator is not None

    def now(self) -> datetime.datetime:
        """The frozen moment, or the real wall clock when not frozen."""
        if self._moment is not None:
            return self._moment
        return datetime.datetime.now(datetime.UTC)

    def stop(self) -> None:
        """Unfreeze (fixture teardown calls this automatically)."""
        self._stop()

    def _stop(self) -> None:
        if self._decorator is not None:
            self._decorator.stop()
            self._decorator = None
            self._factory = None
            self._moment = None


__all__ = ["Clock"]
