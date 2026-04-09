"""Response models returned by each agent layer."""

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
    success: bool = True
    agent: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResult(AgentResponse):
    agent: str = "retrieval"
    data: list[dict[str, Any]] = Field(default_factory=list)
    sources_used: list[str] = Field(default_factory=list)
    cached: bool = False
    fallback_used: bool = False
    actual_source: str = ""

    @property
    def is_empty(self) -> bool:
        return len(self.data) == 0


class SynthesisResult(AgentResponse):
    agent: str = "synthesis"
    documents: list[dict[str, Any]] = Field(default_factory=list)
    entity_name: str | None = None
    entity_ticker: str | None = None


class ValidationCheck(BaseModel):
    """Single machine-readable validation check result."""
    check_name: str
    status: ValidationStatus
    message: str
    field: str | None = None
    expected: Any = None
    actual: Any = None


class ValidationResult(AgentResponse):
    agent: str = "validation"
    status: ValidationStatus = ValidationStatus.PASSED
    checks_run: int = 0
    checks_passed: int = 0
    checks_warned: int = 0
    checks_failed: int = 0
    checks_skipped: int = 0
    checks_unavailable: int = 0
    details: list[ValidationCheck] = Field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.checks_passed / self.checks_run if self.checks_run else 1.0


class OrchestratorResponse(BaseModel):
    success: bool = True
    query: str = ""
    intent: str | None = None
    results: list[AgentResponse] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def all_data(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in self.results:
            if isinstance(r, RetrievalResult):
                out.extend(r.data)
            elif isinstance(r, SynthesisResult):
                out.extend(r.documents)
        return out
