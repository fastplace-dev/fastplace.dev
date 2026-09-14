"""Project entity — the projects module's aggregate root."""

from __future__ import annotations

from fastplace.orm import Field, Model, has_many


class Project(Model):
    __tablename__ = "projects"

    # Cross-module side effect (blueprint §3 rule 4): creation dispatches the
    # ``project_created`` domain event; the consumer job lives in app/jobs/.
    __dispatches__ = {"created": "project_created"}

    id: int = Field(primary_key=True)
    name: str
    # Free-form text up to 2000 chars at the edge — TEXT, not VARCHAR(255).
    description: str = Field(text=True, default="")

    tasks: list[Task] = has_many("Task", back_populates="project")  # noqa: F821
