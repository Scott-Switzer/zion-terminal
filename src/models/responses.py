"""Response models returned by each agent layer.

Every agent wraps its output in one of these envelopes so the orchestrator
and CLI always see a consistent structure.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ValidationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"


class AgentResponse(BaseModel):
    """Base response envelope shared by all agents."""

    success: bool = True
    agent: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResult(AgentResponse):
    """Returned by the retrieval agent after fetching from data sources."""

    agent: str = "retrieval"
    data: list[dict[str, Any]] = Field(default_factory=list)
    sources_used: list[str] = Field(default_factory=list)
    cached: bool = False

    @property
    def is_empty(self) -> bool:
        return len(self.data) == 0


class SynthesisResult(AgentResponse):
    """Returned by the synthesis agent after generating documents."""

    agent: str = "synthesis"
    documents: list[dict[str, Any]] = Field(default_factory=list)
    entity_name: str | None = None
    entity_ticker: str | None = None


class ValidationResult(AgentResponse):
    """Returned by the validation agent after checking data or documents."""

    agent: str = "validation"
    status: ValidationStatus = ValidationStatus.PASSED
    checks_run: int = 0
    checks_passed: int = 0
    checks_failed: int = 0
    details: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.checks_run == 0:
            return 1.0
        return self.checks_passed / self.checks_run


class OrchestratorResponse(BaseModel):
    """Top-level response from the orchestrator combining agent outputs."""

    success: bool = True
    query: str = ""
    intent: str | None = None
    results: list[AgentResponse] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def all_data(self) -> list[dict[str, Any]]:
        """Flatten all data from retrieval results."""
        out: list[dict[str, Any]] = []
        for r in self.results:
            if isinstance(r, RetrievalResult):
                out.extend(r.data)
        return out
