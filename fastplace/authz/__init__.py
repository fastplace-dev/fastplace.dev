"""Fastplace authorization — Gate, policies, and verdicts (spec §4.15)."""

from fastplace.authz.gate import BoundGate, Gate, gate
from fastplace.authz.loader import import_gates
from fastplace.authz.response import Response

__all__ = ["BoundGate", "Gate", "Response", "gate", "import_gates"]
