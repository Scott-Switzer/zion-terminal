"""Team-compatible document models for the cleaning/validation pipeline.

Bridges the private repo's rich FilingPipelineResult with the team repo's
simpler document abstraction.  Teammates can work with CleanedDocument
without knowing about the filing pipeline internals.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class CleanedDocument(BaseModel):
    """A processed SEC filing in team-compatible format.

    Created from ``FilingPipelineResult.to_cleaned_document()`` or
    directly by teammates building parallel workflows.
    """

    doc_id: str = ""
    ticker: str = ""
    form: str = ""
    filing_date: str = ""
    source: str = "sec_edgar"
    markdown: str = ""
    markdown_char_count: int = 0
    sections: list[dict[str, Any]] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_pipeline_result(cls, result: Any) -> "CleanedDocument":
        """Convert a FilingPipelineResult to a CleanedDocument."""
        doc_id = f"{result.ticker}_{result.form}_{result.filing_date}".replace(" ", "_")
        return cls(
            doc_id=doc_id,
            ticker=result.ticker,
            form=result.form,
            filing_date=result.filing_date,
            markdown=result.markdown,
            markdown_char_count=result.markdown_char_count,
            sections=[
                {"item": s.item, "title": s.title, "char_count": s.char_count}
                for s in (result.sections or [])
            ],
            verification=result.verification,
            metadata=result.pipeline_metadata,
        )
