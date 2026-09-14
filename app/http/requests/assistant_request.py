"""HTTP-edge validation for the assistant endpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: Conversation depth the agent loop accepts — history is attacker-supplied
#: input, so it is bounded (DoS) and role-restricted (prompt injection).
MAX_HISTORY_TURNS = 20


class HistoryTurn(BaseModel):
    """One prior turn. `system` is rejected: client input must never
    rewrite the agent's instructions."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class AssistantRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    history: list[HistoryTurn] | None = Field(default=None, max_length=MAX_HISTORY_TURNS)
