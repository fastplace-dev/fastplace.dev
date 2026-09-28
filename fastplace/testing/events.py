"""Events fake — record dispatches instead of delivering them."""

from __future__ import annotations

from typing import Any

from fastplace.events import DomainEvent


class FakeEvents:
    """Replaces ``fastplace.events.dispatch`` for the duration of a test.

    Every dispatch is recorded; listeners do not run and the queue is never
    touched, so a test asserts on the facts the app reported, not on the
    side effects those facts trigger.

    Note the binding rule: call ``fastplace.events.dispatch(...)`` (module
    attribute) or import it inside the function under test — a module-level
    ``from fastplace.events import dispatch`` captured before the fixture
    installed the fake keeps the original function.
    """

    def __init__(self) -> None:
        self.dispatched: list[DomainEvent] = []

    async def record(self, event: DomainEvent, *, to_queue: bool | None = None) -> None:
        self.dispatched.append(event)

    def assert_dispatched(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        times: int | None = None,
    ) -> list[DomainEvent]:
        matches = self._filter(name, payload)
        if times is not None:
            if len(matches) != times:
                raise AssertionError(
                    f"expected {times} dispatch(es) of {name!r}, got {len(matches)}; "
                    f"dispatched: {self._describe()}"
                )
        elif not matches:
            raise AssertionError(
                f"expected at least one dispatch of {name!r}; dispatched: {self._describe()}"
            )
        return matches

    def assert_not_dispatched(self, name: str) -> None:
        matches = self._filter(name, None)
        if matches:
            raise AssertionError(
                f"expected no dispatch of {name!r}, found {len(matches)}: {self._describe()}"
            )

    def assert_dispatched_count(self, count: int) -> None:
        if len(self.dispatched) != count:
            raise AssertionError(
                f"expected {count} total dispatch(es), got {len(self.dispatched)}; "
                f"dispatched: {self._describe()}"
            )

    def _filter(self, name: str, payload: dict[str, Any] | None) -> list[DomainEvent]:
        return [
            event
            for event in self.dispatched
            if event.name == name
            and (payload is None or all(event.payload.get(k) == v for k, v in payload.items()))
        ]

    def _describe(self) -> str:
        if not self.dispatched:
            return "<nothing>"
        return "\n".join(f"  - {event.name}({event.payload})" for event in self.dispatched)


__all__ = ["FakeEvents"]
