from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class FlowSlot(BaseModel):
    key: str
    value: Optional[Any] = None
    filled: bool = False


class FlowState(BaseModel):
    """Runtime state of a multi-turn flow. Stored in Redis."""
    session_id: str
    message_id: str
    user_id: int = 0                # owner of this flow
    intent: str
    flow_name: str
    current_step: str
    slots: dict[str, Any] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)
    attempts: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
