"""Portable date/time columns on every backend.

The blueprint's compatibility-matrix "date/time" category. Values are naive
(the framework convention is UTC-in/UTC-out) and carry no microseconds —
MySQL's default DATETIME truncates fractional seconds, so the shared
contract is whole-second precision.
"""

from __future__ import annotations

import datetime

from fastplace.db import db
from fastplace.orm import Field, Model


async def test_datetime_date_and_time_round_trip(backend):
    class Milestone(Model):
        __tablename__ = "port_milestones"

        id: int = Field(primary_key=True)
        name: str = ""
        reached_at: datetime.datetime = Field(default_factory=lambda: datetime.datetime(1970, 1, 1))
        on_day: datetime.date = datetime.date(1970, 1, 1)
        window: datetime.time = datetime.time(0, 0)

    await db.create_all()

    stamp = datetime.datetime(2026, 9, 14, 12, 34, 56)
    created = await Milestone.create(
        name="v1",
        reached_at=stamp,
        on_day=datetime.date(2026, 9, 14),
        window=datetime.time(9, 30, 0),
    )

    fetched = await Milestone.find(created.id)
    assert fetched.reached_at == stamp
    assert fetched.on_day == datetime.date(2026, 9, 14)
    assert fetched.window == datetime.time(9, 30, 0)


async def test_timestamps_auto_stamp_and_survive_refresh(backend):
    class Log(Model):
        __tablename__ = "port_logs"

        id: int = Field(primary_key=True)
        message: str = ""

    await db.create_all()

    entry = await Log.create(message="first")
    assert isinstance(entry.created_at, datetime.datetime)
    assert isinstance(entry.updated_at, datetime.datetime)

    await entry.update(message="second")
    await entry.refresh()
    # updated_at moves forward (or at minimum stays a real timestamp); the
    # round trip never returns strings or Nones on any backend.
    assert isinstance(entry.updated_at, datetime.datetime)
    assert entry.updated_at >= entry.created_at
