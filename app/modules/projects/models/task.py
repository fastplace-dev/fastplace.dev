"""Task entity — belongs to a project; the dashboard workflow's unit of work."""

from __future__ import annotations

import datetime

from fastplace.orm import Field, Model, belongs_to


class Task(Model):
    __tablename__ = "tasks"

    id: int = Field(primary_key=True)
    # Indexed: every project page and count aggregates by this column.
    project_id: int = Field(foreign_key="projects.id", index=True)
    title: str
    completed: bool = False
    due_date: datetime.date | None = None

    project: Project = belongs_to("Project", back_populates="tasks")  # noqa: F821
