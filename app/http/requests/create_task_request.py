"""HTTP-edge validation for task creation."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class CreateTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255, pattern=r"\S")
    due_date: datetime.date | None = None
