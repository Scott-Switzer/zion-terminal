# Zion Tool Contract V3

`zion-tool-contract-v3` is additive. `zion-tool-contract-v2` and its pinned hash remain frozen for real-world compatibility and rollback.

Canonical V3 hash:

```text
347dc8e0973918fafcf908145cd9135c6e5d11e4a5eecb22b2390bcb97fbc976
```

V3 keeps the same eleven deterministic tools and adds explicit world-independent execution:

- real: `us-public-markets`, delegated to the frozen real serving path;
- synthetic: an explicitly configured world ID resolved through its `CURRENT.json` pointer.

The additive HTTP surface is:

- `GET /v3/capabilities`
- `POST /v3/tools/{tool}` with `{world, arguments, as_of?, synthetic_release_id?}`.

Synthetic loading is fail-closed. Zion reads only the immutable public release artifacts under `gold/synthetic-worlds/releases/{release_id}/public/`, verifies the manifest identity, QC certificate, artifact hashes, world/version identity, timestamps, and public-only boundary. Hidden generator state is never read by the serving adapter.

Synthetic results carry the synthetic release ID, producer identity, QC certification identity, world identity, temporal contract identity, and V3 contract identity. PIT filtering uses each public row's `available_at`; calculations remain exact and world-independent. Cross-world mixing is impossible within one V3 envelope because the envelope has exactly one world reference.

The existing V2 routes, MCP surface, production pointer, and real financial values are unchanged.
