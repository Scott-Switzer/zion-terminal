"""Unified filing pipeline: retrieval → conversion → segmentation → verification.

This is the single live path for all SEC filing processing.
The SEC adapter's private _html_to_markdown() is retired in favor of this pipeline.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any

from zion_terminal.pipeline.converter import FilingConverter
from zion_terminal.pipeline.segmenter import FilingSegmenter, FilingSection
from zion_terminal.pipeline.verification import FilingVerifier

logger = logging.getLogger(__name__)

@dataclass
class FilingPipelineResult:
    """Structured result from the unified filing pipeline."""
    success: bool = False
    ticker: str = ""
    form: str = ""
    filing_date: str = ""
    
    # Raw source
    raw_html: str = ""
    raw_char_count: int = 0
    
    # Conversion stage
    markdown: str = ""
    markdown_char_count: int = 0
    converter_engine: str = ""  # "dom" or "regex"
    
    # Segmentation stage
    sections: list[FilingSection] = field(default_factory=list)
    section_count: int = 0
    
    # Verification stage
    verification: dict[str, Any] = field(default_factory=dict)
    
    # Pipeline metadata
    pipeline_metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_cleaned_document(self) -> "CleanedDocument":
        """Convert to a team-compatible CleanedDocument."""
        from zion_terminal.models.documents import CleanedDocument
        return CleanedDocument.from_pipeline_result(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "ticker": self.ticker,
            "form": self.form,
            "filing_date": self.filing_date,
            "markdown": self.markdown,
            "markdown_char_count": self.markdown_char_count,
            "converter_engine": self.converter_engine,
            "section_count": self.section_count,
            "sections_summary": [
                {"item": s.item, "title": s.title, "char_count": s.char_count}
                for s in self.sections
            ],
            "verification": self.verification,
            "pipeline_metadata": self.pipeline_metadata,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class FilingPipeline:
    """Unified pipeline: HTML → markdown → sections → verification hooks.
    
    This is the ONLY path for filing conversion. The SEC adapter must use this.
    """
    
    def __init__(self, max_length: int = 200_000) -> None:
        self._converter = FilingConverter(max_length=max_length)
        self._segmenter = FilingSegmenter()
        self._verifier = FilingVerifier()
    
    def process(
        self,
        html: str,
        ticker: str = "",
        form: str = "",
        filing_date: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> FilingPipelineResult:
        """Run the full filing pipeline on raw HTML content."""
        result = FilingPipelineResult(
            ticker=ticker,
            form=form,
            filing_date=filing_date,
            raw_html=html[:1000],  # Store only a preview of raw HTML
            raw_char_count=len(html),
        )
        
        # Stage 1: Conversion
        try:
            conv = self._converter.convert(html, metadata=metadata or {})
            result.markdown = conv["markdown"]
            result.markdown_char_count = conv["char_count"]
            result.converter_engine = conv["metadata"].get("converter", "unknown")
            result.pipeline_metadata["conversion"] = {
                "engine": result.converter_engine,
                "truncated": conv["metadata"].get("truncated", False),
                "char_count": conv["char_count"],
            }
        except Exception as exc:
            result.errors.append(f"Conversion failed: {exc}")
            return result
        
        # Stage 2: Segmentation
        try:
            sections = self._segmenter.segment(result.markdown)
            result.sections = sections
            result.section_count = len(sections)
            result.pipeline_metadata["segmentation"] = {
                "section_count": len(sections),
                "sections": [
                    {"item": s.item, "title": s.title, "char_count": s.char_count}
                    for s in sections
                ],
            }
        except Exception as exc:
            result.warnings.append(f"Segmentation failed: {exc}")
        
        # Stage 3: Live verification (structural + XBRL + reconciliation)
        try:
            xbrl_url = (metadata or {}).get("xbrl_url")
            yahoo_data = (metadata or {}).get("yahoo_data")
            company_facts = (metadata or {}).get("company_facts")
            verification_result = self._verifier.verify(
                markdown=result.markdown,
                sections=result.sections if result.sections else None,
                ticker=ticker,
                form=form,
                xbrl_url=xbrl_url,
                yahoo_data=yahoo_data,
                company_facts=company_facts,
                filing_date=filing_date,
            )
            result.verification = verification_result.to_dict()
            result.pipeline_metadata["verification"] = {
                "status": verification_result.status,
                "xbrl_status": verification_result.xbrl_status,
                "structural_checks_count": len(verification_result.structural_checks),
            }
            if verification_result.warnings:
                result.warnings.extend(verification_result.warnings)
        except Exception as exc:
            result.warnings.append(f"Verification failed: {exc}")
            result.verification = {
                "status": "error",
                "error": str(exc),
            }

        result.success = True
        result.pipeline_metadata["source_role"] = "primary"
        result.pipeline_metadata["source"] = "sec_edgar"
        return result
