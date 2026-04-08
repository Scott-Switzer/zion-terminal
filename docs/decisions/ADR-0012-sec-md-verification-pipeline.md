# ADR-0012: SEC → Markdown → Verification Pipeline

## Status
Accepted

## Context
Filing conversion and verification were adjacent but disconnected features. A filing could be converted without any verification, and verification ran as a separate tool.

## Decision
The filing pipeline is now a three-stage process: conversion → segmentation → verification. Verification runs inline as part of `FilingPipeline.process()` via `FilingVerifier`.

## Consequences
- Every filing conversion includes structural verification
- XBRL verification runs when Arelle is installed and a XBRL URL is available
- Verification results are part of the `FilingPipelineResult`
- Cross-source hooks exist for future Yahoo/FRED comparison
