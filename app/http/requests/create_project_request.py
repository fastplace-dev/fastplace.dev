"""HTTP-edge validation for project creation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255, pattern=r"\S")
    description: str = Field(default="", max_length=2000)
