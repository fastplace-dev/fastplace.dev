"""HTTP-edge validation for knowledge ingestion."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class IngestKnowledgeRequest(BaseModel):
    title: str = Field(default="", max_length=512)
    content: str = Field(default="", max_length=50_000)

    @model_validator(mode="after")
    def _needs_something_indexable(self) -> IngestKnowledgeRequest:
        if not (self.title.strip() or self.content.strip()):
            raise ValueError("a knowledge item needs a title or content")
        return self
