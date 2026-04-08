# ADR-009: Surface LLM-Assisted Flag in Response Metadata

## Status
Accepted

## Context
When the rule-based intent parser cannot resolve a query, it falls back to the LLM for parsing (if an LLM provider is configured).  The parser stored `_llm_assisted = True` in `ParsedIntent.params`, but the orchestrator never propagated this to the `OrchestratorResponse.metadata`.

Callers had no way to know whether a response was produced with LLM assistance or purely deterministically.

## Decision
The orchestrator now checks `parsed.params.get("_llm_assisted")` and, when true, sets `metadata["llm_assisted"] = True` on the response.

The field is only present when the LLM was actually used.  Absence means deterministic parsing.

## Consequences
- CLI and API consumers can inspect `response.metadata["llm_assisted"]` to audit which responses involved LLM inference.
- Supports the project's goal of keeping LLM usage optional and transparent.
- No behavior change — this is metadata surfacing only.
