"""Filing conversion pipeline — HTML→markdown with section segmentation.

This module provides a structured pipeline for converting SEC filing HTML
into section-segmented markdown, suitable for downstream analysis,
search indexing, or LLM consumption.

Status: experimental. Works for standard 10-K and 10-Q formats.
"""

from zion_terminal.pipeline.converter import FilingConverter
from zion_terminal.pipeline.segmenter import FilingSegmenter, FilingSection

__all__ = ["FilingConverter", "FilingSegmenter", "FilingSection"]
