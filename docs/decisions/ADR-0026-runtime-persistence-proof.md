# ADR-0026: Runtime Persistence Proof (v0.8.2)

**Date:** 2026-04-08  
**Status:** Accepted

## Context

The persistence layer (CleanedDocument + DocumentStore) was wired into the
orchestrator's `get_filing_markdown()` path in v0.8.1, but the tests only
checked method existence and property availability — not actual runtime
behavior. This was a weak proof that could mask real integration failures.

## Problem

Tests like `test_orchestrator_has_doc_store` and
`test_persist_filing_document_method_exists` verify that the code structure
is correct, but they don't prove that:
1. A real call to `get_filing_markdown()` triggers persistence
2. The persisted document contains the expected data
3. Persistence is skipped on failure
4. The stored document can be listed and retrieved

## Change

Replaced the 2 weak existence-checking tests with 4 runtime behavior tests:

1. `test_persist_creates_document_in_store` — calls `_persist_filing_document`
   with a mock response, then retrieves from DocumentStore and verifies content
2. `test_get_filing_markdown_calls_persist` — mocks `_fetch_and_validate`,
   verifies `_persist_filing_document` is called by `get_filing_markdown`
3. `test_persist_does_not_run_on_failure` — verifies persistence is skipped
   when the response has `success=False`
4. `test_doc_store_list_after_persist` — verifies `list_docs()` returns the
   stored document after persistence

## Why This Fits the Repo

The private repo claims live persistence as a team-compatibility feature.
Weak tests undermine that claim. Runtime tests prove the behavior is real
and give teammates confidence that the workflow actually works.
