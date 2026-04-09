# Compatibility Layer Status (v0.8.2)

## What Is Live and Proven

| Component | Status | Evidence |
|-----------|--------|----------|
| `CleanedDocument` | **Live** | Created in `_persist_filing_document()`, tested with runtime roundtrip |
| `DocumentStore` | **Live** | Initialized in Orchestrator, used by `get_filing_markdown()`, tested |
| `to_cleaned_document()` | **Live** | Method on FilingPipelineResult, tested |
| `doc_store` property | **Live** | Exposed on Orchestrator for teammate use |

## What Is Transitional Scaffolding

| Component | Status | Reason |
|-----------|--------|--------|
| `RetrievalRequest` (contracts.py) | **Not used** | Defined but never imported outside its own file |
| `CleanedResult` (contracts.py) | **Not used** | Defined but never imported outside its own file |

These contracts were created as aspirational boundary contracts for team
convergence. They are not yet integrated into any live path. They exist so
teammates can see the intended interface shape, but the private repo's
actual live interfaces are richer (e.g. `OrchestratorResponse` has more
fields than `CleanedResult`).

**Decision:** Keep as reference contracts but do not claim they are integrated.
They may be adopted when the team repo and private repo converge further.

## What Diverges From the Team Repo

| Private Repo | Team Repo | Status |
|-------------|-----------|--------|
| `FilingPipelineResult` | `CleanedDocument` | Bridged via `to_cleaned_document()` |
| `CacheManager` + `DocumentStore` | `Cache` (unified) | Side-by-side, both SQLite-backed |
| `ParsedIntent` | `QueryIntent` | Different field sets, not yet unified |
| `OrchestratorResponse` | `DataResponse` | Different schemas, not yet unified |
