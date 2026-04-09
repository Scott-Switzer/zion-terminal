"""Team-compatible schema contracts for agent boundaries.

These contracts define stable interfaces between retrieval, cleaning,
validation, and cache layers.  Teammates can build against these without
needing to understand the internal pipeline or adapter details.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RetrievalRequest(BaseModel):
    """Standardized retrieval request contract."""

    source: str
    ticker: str = ""
    action: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class CleanedResult(BaseModel):
    """Result of the cleaning/processing pipeline."""

    success: bool = True
    doc_id: str = ""
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
