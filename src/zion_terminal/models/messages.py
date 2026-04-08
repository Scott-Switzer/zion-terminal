"""Standardized inter-agent message format."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class AgentRole(str, Enum):
    ORCHESTRATOR = "orchestrator"
    RETRIEVAL = "retrieval"
    SYNTHESIS = "synthesis"
    VALIDATION = "validation"


class MessageType(str, Enum):
    QUERY = "query"
    DATA_RESPONSE = "data_response"
    ERROR = "error"
    VALIDATION = "validation"
    COMMAND = "command"


class AgentMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sender: AgentRole
    receiver: AgentRole
    message_type: MessageType
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    parent_id: str | None = None

    model_config = ConfigDict(use_enum_values=True)
