# Security and entitlement review

The new research routes require the existing server-side bearer token. They accept controlled GET parameters, not arbitrary SQL, provider URLs, bucket names or object keys. Research manifests, SEC manifests and dependent objects are hash pinned. Signed pagination binds both query and source versions. Read and response budgets fail explicitly. No research request writes to R2.

Permitted market integration is private owner Alpaca historical research under the documented personal-use terms. Original decimal tokens are recovered natively from stored provider JSON; price records and exports must not be sent to an LLM. Original FLOAT64 projections are not represented as exact source values. Provider availability timestamps are unknown, so retrospective prices do not claim point-in-time validity. Current listing identifiers do not establish historical issuer links.

The existing `/v1/market/*` routes require authentication when `OWNER_ONLY_MARKET_DATA=true`; paths remain available to authenticated clients. Existing public SEC behavior is unchanged. Licensed WRDS and other datasets without established rights are excluded from the research manifest and do not become queryable through the new interface. The shared archive binding remains needed for existing approved public SEC ZIP access; it is not authorization to serve neighboring licensed objects.

## Recorded boundary incident

During metadata investigation, an overly broad local text search matched a legacy analytical artifact outside the intended metadata scope and returned derived numerical fields into tool context. Their provenance was not inspected, so classification remains `UNKNOWN_POTENTIALLY_LICENSED_DERIVED`. No raw licensed payload was opened, decoded or queried. The search stopped, scope was narrowed to explicit metadata, code and public SEC paths, and the artifact was excluded from integration. This review does not claim that the session had zero potentially licensed derived-data exposure. No values are reproduced here.

## Remaining limitations

Historical identity support uses literal dated SEC cover evidence and does not invent continuous ticker intervals, delisting histories or corporate-action completeness. Unknown rights on other corporate-action collections prevent their activation. Exact shares, security class, availability and complete enterprise-value components are required for market-linked valuation; unsupported inputs return unavailable status. TLS and token-provisioning permission failures remain independent operational issues, not grounds to widen credentials or routing.
