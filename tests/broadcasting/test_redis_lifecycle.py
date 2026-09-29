"""Kernel lifecycle wiring for the redis broadcast driver.

The shutdown hook closes the process bus when BROADCAST_DRIVER=redis so a
graceful stop releases the broker connection and listener task. No live
redis is needed here — the hook only calls ``close()`` on whatever bus the
factory holds, and a never-started bus closes as a no-op.
"""

from __future__ import annotations

import pytest

from fastplace import broadcasting
from fastplace.http import lifecycle
from fastplace.http.kernel import _register_broadcast_lifecycle


@pytest.fixture(autouse=True)
def _isolated_lifecycle(monkeypatch):
    monkeypatch.delenv("BROADCAST_DRIVER", raising=False)
    lifecycle.reset()
    broadcasting.reset_broadcasting()
    yield
    lifecycle.reset()
    broadcasting.reset_broadcasting()


class FakeBus:
    """Records close() calls — the only protocol the hook relies on."""

    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class TestRegisterBroadcastLifecycle:
    async def test_redis_driver_closes_the_bus_on_shutdown(self, monkeypatch):
        bus = FakeBus()
        broadcasting.set_broadcast_bus(bus)  # type: ignore[arg-type]
        monkeypatch.setenv("BROADCAST_DRIVER", "redis")

        _register_broadcast_lifecycle()
        await lifecycle.run_shutdown()

        assert bus.closed == 1

    async def test_memory_driver_registers_no_hook(self):
        # The memory bus holds no broker resources — no shutdown hook, so
        # memory-driver apps keep the hook list untouched.
        hooks_before = list(lifecycle._shutdown_hooks)

        _register_broadcast_lifecycle()

        assert lifecycle._shutdown_hooks == hooks_before
