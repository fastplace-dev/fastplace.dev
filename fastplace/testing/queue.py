"""Queue fake — record-and-assert wrapper over the in-memory driver.

Dispatches flow through the ordinary :class:`~fastplace.queue.MemoryQueue`
machinery (validation, envelopes, unique keys), so a fake-queue test
exercises the same code path production code calls; the fake only records
what was pushed and what actually ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastplace.queue import MemoryQueue


@dataclass
class PushedJob:
    """One dispatch recorded by the fake."""

    name: str
    kwargs: dict[str, Any]
    options: dict[str, Any] = field(default_factory=dict)


class FakeQueue(MemoryQueue):
    """The memory queue plus assertion helpers (``assert_pushed`` et al.)."""

    def __init__(self) -> None:
        super().__init__()
        self.pushed: list[PushedJob] = []
        self.executed: list[PushedJob] = []

    async def _enqueue_memory(
        self,
        name: str,
        kwargs: dict[str, Any],
        envelope: dict[str, Any],
        delay: float = 0.0,
        unique: bool = False,
    ) -> Any:
        handle = await super()._enqueue_memory(name, kwargs, envelope, delay=delay, unique=unique)
        # A suppressed unique re-dispatch returns None — nothing was pushed.
        if handle is not None:
            self.pushed.append(
                PushedJob(name=name, kwargs=dict(kwargs), options=dict(envelope or {}))
            )
        return handle

    async def _run_one(self, item: Any) -> None:
        await super()._run_one(item)
        handle = self.dispatched.get(item.key) if item.key else None
        if handle is not None and handle.status == "completed":
            self.executed.append(
                PushedJob(
                    name=item.name, kwargs=dict(item.kwargs), options=dict(item.options or {})
                )
            )

    # -- draining ---------------------------------------------------------

    async def run(self, honor_sentinel: bool = True) -> int:
        """Execute every queued job; returns how many ran."""
        return await self.run_pending(honor_sentinel=honor_sentinel)

    # -- assertions -------------------------------------------------------

    def assert_pushed(
        self,
        name: str | None = None,
        match: dict[str, Any] | None = None,
        times: int | None = None,
    ) -> list[PushedJob]:
        matches = self._filter(self.pushed, name, match)
        if times is not None:
            if len(matches) != times:
                raise AssertionError(
                    f"expected {times} push(es) of {_label(name, match)}, "
                    f"got {len(matches)}; pushed: {_label_all(self.pushed)}"
                )
        elif not matches:
            raise AssertionError(
                f"expected at least one push of {_label(name, match)}; "
                f"pushed: {_label_all(self.pushed)}"
            )
        return matches

    def assert_not_pushed(
        self, name: str | None = None, match: dict[str, Any] | None = None
    ) -> None:
        matches = self._filter(self.pushed, name, match)
        if matches:
            raise AssertionError(
                f"expected no push of {_label(name, match)}, found {len(matches)}: "
                f"{_label_all(matches)}"
            )

    def assert_pushed_count(self, count: int) -> None:
        if len(self.pushed) != count:
            raise AssertionError(
                f"expected {count} total push(es), got {len(self.pushed)}; "
                f"pushed: {_label_all(self.pushed)}"
            )

    def assert_nothing_pushed(self) -> None:
        self.assert_pushed_count(0)

    def assert_executed(
        self,
        name: str | None = None,
        match: dict[str, Any] | None = None,
        times: int | None = None,
    ) -> list[PushedJob]:
        matches = self._filter(self.executed, name, match)
        if times is not None:
            if len(matches) != times:
                raise AssertionError(
                    f"expected {times} execution(s) of {_label(name, match)}, "
                    f"got {len(matches)}; executed: {_label_all(self.executed)}"
                )
        elif not matches:
            raise AssertionError(
                f"expected at least one execution of {_label(name, match)}; "
                f"executed: {_label_all(self.executed)}"
            )
        return matches

    def assert_not_executed(
        self, name: str | None = None, match: dict[str, Any] | None = None
    ) -> None:
        matches = self._filter(self.executed, name, match)
        if matches:
            raise AssertionError(
                f"expected no execution of {_label(name, match)}, found {len(matches)}"
            )

    @staticmethod
    def _filter(
        jobs: list[PushedJob], name: str | None, match: dict[str, Any] | None
    ) -> list[PushedJob]:
        return [
            job
            for job in jobs
            if (name is None or job.name == name)
            and (match is None or all(job.kwargs.get(k) == v for k, v in match.items()))
        ]


def _label(name: str | None, match: dict[str, Any] | None) -> str:
    parts = [f"name={name!r}"] if name is not None else []
    if match is not None:
        parts.append(f"match={match!r}")
    return f"({', '.join(parts)})" if parts else "(any job)"


def _label_all(jobs: list[PushedJob]) -> str:
    if not jobs:
        return "<nothing>"
    return "\n".join(f"  - {job.name}({job.kwargs})" for job in jobs)


__all__ = ["FakeQueue", "PushedJob"]
