"""Shared doctor check-runner: one Rich table, one exit policy.

Every ``*:doctor`` / health command in the roadmap family renders through
``run_checks`` so tables, colors, and exit codes stay identical.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # annotations stay lazy; the runtime imports are function-local
    from rich.console import Console

Status = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class Check:
    """One doctor finding. ``detail``/``fix`` carry masked values only."""

    name: str
    status: Status
    detail: str = ""
    fix: str = ""


#: A check runs with no arguments and returns one Check or spreads a list.
CheckFunc = Callable[[], "Check | list[Check]"]

_STATUS_LABEL: dict[Status, str] = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
_STATUS_STYLE: dict[Status, str] = {"pass": "green", "warn": "yellow", "fail": "red"}


def _fallback_name(fn: CheckFunc) -> str:
    """Row name for a check that raised: ``_check_env_parse`` -> ``env_parse``."""
    raw = getattr(fn, "__name__", "check")
    return raw.removeprefix("_check_").lstrip("_")


def run_checks(
    title: str,
    checks: Sequence[CheckFunc],
    *,
    console: Console | None = None,
) -> int:
    """Execute checks in order, render one Rich table, return the exit code.

    A raising check collects as FAIL (``Type: message``) — a doctor never
    crashes mid-table; remaining checks still run. Any FAIL returns 1;
    WARN alone returns 0 (warnings must not block CI).
    """
    from rich.table import Table

    if console is None:
        from fastplace.console import console as _default_console

        console = _default_console

    rows: list[Check] = []
    for fn in checks:
        try:
            outcome = fn()
        except Exception as exc:  # noqa: BLE001 - a doctor never dies mid-table
            outcome = Check(
                _fallback_name(fn), "fail", detail=f"{type(exc).__name__}: {exc}"
            )
        if isinstance(outcome, Check):
            rows.append(outcome)
        else:
            rows.extend(outcome)

    table = Table(title=title)
    table.add_column("CHECK")
    table.add_column("STATUS")
    table.add_column("DETAIL")
    table.add_column("FIX", style="dim")
    for check in rows:
        table.add_row(
            check.name,
            f"[{_STATUS_STYLE[check.status]}]{_STATUS_LABEL[check.status]}[/]",
            check.detail,
            check.fix,
        )
    console.print(table)

    passed = sum(1 for r in rows if r.status == "pass")
    warned = sum(1 for r in rows if r.status == "warn")
    failed = sum(1 for r in rows if r.status == "fail")
    console.print(f"{passed} pass · {warned} warn · {failed} fail")

    return 1 if failed else 0
