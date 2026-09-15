# World routing

Zion routes `FinancialQueryRequestV1` by `world.world_type` through `WorldRegistry`. Real and synthetic handlers are external JSON-compatible backends; Zion does not import `financial-system-core`, own a financial data lake, or duplicate producer truth.

Handlers must return the same `FinancialQueryResponseV1` shape and the response world identity must match the request. LLM use remains optional and outside deterministic native acceptance tests.
