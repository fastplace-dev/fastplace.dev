"""Portable concurrency — parallel workloads on every backend.

The blueprint's compatibility-matrix "concurrency" category: concurrent
standalone writes, a failing transaction racing a committing one, and mixed
read/write traffic. SQLite proves the serializing pool keeps file-backed
check-then-act races away; server backends prove normal pooled behavior.
"""

from __future__ import annotations

import asyncio

from fastplace.db import db
from fastplace.orm import Field, Model


async def test_concurrent_standalone_creates_all_land(backend):
    class Raffle(Model):
        __tablename__ = "port_raffles"

        id: int = Field(primary_key=True)
        ticket: str = ""

    await db.create_all()

    await asyncio.gather(
        *(Raffle.create(ticket=f"t{index:02d}") for index in range(20)),
    )

    tickets = sorted(r.ticket for r in await Raffle.all())
    assert len(tickets) == 20
    assert len(set(tickets)) == 20  # no id collisions, no lost writes


async def test_failing_transaction_racing_a_committing_create(backend):
    class Ledger(Model):
        __tablename__ = "port_ledgers"

        id: int = Field(primary_key=True)
        entry: str = ""

    await db.create_all()

    async def failing_tx():
        async with db.transaction():
            await Ledger.create(entry="rolled-back")
            await asyncio.sleep(0.05)
            raise RuntimeError("boom")

    results = await asyncio.gather(
        failing_tx(),
        Ledger.create(entry="committed"),
        return_exceptions=True,
    )
    assert isinstance(results[0], RuntimeError)

    entries = [row.entry for row in await Ledger.all()]
    assert entries == ["committed"]


async def test_reads_interleaved_with_writes_stay_consistent(backend):
    """Concurrent readers never observe a torn state: every count is a
    whole number between zero and the final total, and the final total
    reflects every committed write. (Read-modify-write races are the
    caller's business — the ORM contract is committed-state visibility.)"""

    class Metric(Model):
        __tablename__ = "port_metrics"

        id: int = Field(primary_key=True)
        label: str = ""

    await db.create_all()

    async def write(index: int) -> None:
        await Metric.create(label=f"m{index:02d}")

    async def read_counts() -> list[int]:
        counts = []
        for _ in range(5):
            counts.append(await Metric.count())
            await asyncio.sleep(0)
        return counts

    writers = [write(index) for index in range(20)]
    results = await asyncio.gather(read_counts(), *writers)
    observed = results[0]

    assert await Metric.count() == 20
    assert all(0 <= count <= 20 for count in observed)
