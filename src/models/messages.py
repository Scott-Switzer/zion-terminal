"""Standardized inter-agent message format.

All agent communication flows through this message schema so that agents
remain decoupled: the orchestrator only needs to know *what* to ask, not
how each agent works internally.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class AgentRole(str, Enum):
    """Identifies which agent produced or should receive a message."""

    ORCHESTRATOR = "orchestrator"
    RETRIEVAL = "retrieval"
    SYNTHESIS = "synthesis"
    VALIDATION = "validation"


class MessageType(str, Enum):
    """The kind of payload carried in the message."""

    QUERY = "query"               # user / orchestrator request
    DATA_RESPONSE = "data_response"  # agent returning data
    ERROR = "error"               # agent reporting a failure
    VALIDATION = "validation"     # validation result
    COMMAND = "command"            # orchestrator instruction to an agent


class AgentMessage(BaseModel):
    """Envelope for all inter-agent communication.

    Every message gets a unique id, a timestamp, and a clear sender/receiver
    so that conversations can be logged, replayed, and debugged.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sender: AgentRole
    receiver: AgentRole
    message_type: MessageType
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    parent_id: str | None = None  # links replies to the original request

    model_config = ConfigDict(use_enum_values=True)
