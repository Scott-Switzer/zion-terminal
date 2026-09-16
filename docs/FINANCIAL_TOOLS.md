# Financial Tools V1

Zion exposes one deterministic tool registry to `/v1/query` and `/v1/tools/{tool_name}`. Tools consume published, release-addressed producer artifacts; they do not call market providers or an LLM.

## Tools

- `resolve_entity`: exact symbol resolution in the selected world.
- `get_price`: latest published close and provenance.
- `get_price_history`: bounded daily OHLCV observations, maximum 500 rows.
- `get_fundamentals`: normalized period observations, maximum 40 periods.
- `get_filing`: bounded filing metadata, maximum 20 filings; real world only.
- `get_evidence`: bounded provenance records resolved through producer artifacts.
- `calculate`: allowlisted `change`, `percent_change`, `average`, `min`, `max`, and `basis_point_change` operations.
- `compare`: orchestrates existing retrieval for up to 10 entities.

Each tool result contains `tool`, `world`, `data`, `evidence`, `quality`, and `release`. The natural-language endpoint converts the same result into the established query response contract.

## Published data

Real fundamentals are served from the immutable derived release under `gold/market-terminal/fundamentals/releases/<digest>/`, selected through `control/market-terminal/fundamentals/CURRENT.json`. The builder is `deploy/cloudflare-query/build_read_models.py`; it derives normalized facts from PPE-published SEC artifacts and never changes acquisition.

Synthetic tools read only the certified Market Fuzzer `public/` release. Hidden world state is not a serving input.

## Limits and semantics

Requests are limited to 32 KiB and query text to 2,000 characters. `as_of` filters every observation by its `available_at` timestamp. Missing or unavailable data is reported rather than inferred. Annual and quarterly periods are never silently mixed. The current PPE corpus contains annual AAPL/MSFT/NVDA fundamentals; quarterly results remain unavailable until a published quarterly source is present.

## Examples

```http
POST /v1/tools/get_fundamentals
Content-Type: application/json

{"entity":"AAPL","metrics":["revenue","gross_margin"],"period":"annual","lookback":4,"world":{"world_type":"real","world_id":"us-public-markets"}}
```

```http
POST /v1/tools/calculate
Content-Type: application/json

{"operation":"percent_change","values":[100,125],"world":{"world_type":"real","world_id":"us-public-markets"}}
```

The tool endpoint is intended as a stable machine interface for future clients; no MCP or Slack adapter is included in this milestone.
