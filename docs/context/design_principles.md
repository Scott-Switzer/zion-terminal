# Design Principles

These are non-negotiable architectural decisions.

## 1. Modularity
Each agent handles one specific task. Agents are independently testable, replaceable, and can use different models per task. Errors are isolated and traceable.

## 2. SEC First
SEC filings are the legal record of a company's financial position. They are the primary source of truth. Yahoo Finance is a cross-check, not a source of truth.

## 3. Markdown First
Markdown is the confirmed primary output format (empirically superior for retrieval accuracy vs JSON or TNLM per team benchmarks). All filing content is converted to markdown for downstream consumption.

## 4. Determinism by Default
Synthesis generates identical output from identical inputs (in no-LLM mode). Seeded RNG ensures reproducibility. LLM components are explicitly marked as non-deterministic.

## 5. Honest Validation
If a check passes, it passed. If it was skipped, say "skipped". If a tool is missing, say "unavailable". Never say "passed" when the check didn't run.

## 6. Graceful Degradation
- No LLM? Core retrieval works.
- No Arelle? Filing conversion works, XBRL validation is marked unavailable.
- No FRED key? Everything except macro data works.
- No SEC identity? Everything except SEC data works.

## 7. No Inflated Claims
The system does what it does. Experimental features are labeled experimental. Accuracy claims are backed by benchmarks. Test counts match reality.

## 8. One Path, One Truth
No parallel conversion paths. No duplicate adapters. One live path, benchmarked and tested.

## 9. Cache-First Architecture
Financial data doesn't change by the minute. Cache aggressively. Schema-version the cache so format changes don't serve stale data.

## 10. Pipeline, Not Monolith
Filing processing is a staged pipeline: retrieval → conversion → segmentation → verification → output. Each stage has its own tests and can be improved independently.
