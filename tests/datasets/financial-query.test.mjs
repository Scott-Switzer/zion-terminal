import test from "node:test";
import assert from "node:assert/strict";
import {
  handleFinancialQuery,
  exactParse,
  canonicalMetadata,
} from "../../deploy/datasets/src/financial-query.mjs";
import { digest } from "../../deploy/datasets/src/native.mjs";

const account = "51b88c6a6ef833b3c2ff46e98d5d9356",
  bucket = "financial-system-datasets";
async function fixture() {
  const namespace = "zion_uqf_pilot_" + "a".repeat(12),
    snapshot = "9007199254740993123";
  const metadata = {
    "current-snapshot-id": snapshot,
    snapshots: [
      { "snapshot-id": snapshot, summary: { "total-files-size": "100" } },
    ],
  };
  const metadataHash = await digest(
    JSON.stringify(exactParse(JSON.stringify(metadata))),
  );
  const names = ["issuer_anchor", "sec_concept_facts", "precision_canary"];
  const tables = names.map((name) => ({
    name,
    namespace,
    snapshot_id: snapshot,
    metadata_location: "s3://" + bucket + "/" + name + ".json",
    metadata_canonical_sha256: metadataHash,
    files_bytes_declared: 100,
    rights:
      name === "precision_canary"
        ? "OWNED_SYNTHETIC_TEST_ONLY"
        : "PUBLIC_SEC_ONLY",
  }));
  const policy = {
    schema_version: "zion-financial-query-policy-v1",
    account_id: account,
    bucket,
    namespace,
    catalog_prefix: "c7707ae2-b1e3-11f1-8000-fbcd376acb16",
    allowed_principal: "private-owner",
    allowed_ciks: ["0001477333", "0001543151"],
    tables,
    licensed_datasets_allowed: false,
    promotion_allowed: false,
    source_sha256: "b".repeat(64),
  };
  const raw = JSON.stringify(policy),
    cache = new Map();
  let reads = 0,
    calls = [];
  const state = { changed: false, tooMany: false, numericValue: false };
  const env = {
    BASIN_SQL_TOKEN: "do-not-log-this-secret",
    BASIN_POLICY_KEY: "gold/unified-foundation/policies/test.json",
    BASIN_POLICY_SHA256: await digest(raw),
    FINANCIAL_DATA: {
      get: async (key) => {
        reads++;
        return key.includes("/policies/")
          ? { size: raw.length, text: async () => raw }
          : (cache.get(key) ?? null);
      },
      put: async (key, value, opts) =>
        cache.set(key, {
          size: value.length,
          text: async () => value,
          customMetadata: opts.customMetadata,
        }),
    },
  };
  const fetcher = async (url, options) => {
    assert.equal(options.redirect, "manual", "Workers supports manual, not redirect:error");
    calls.push({ url, options });
    if (url.startsWith("https://catalog.")) {
      const name = url.split("/").at(-1);
      return Response.json({
        "metadata-location": "s3://" + bucket + "/" + name + ".json",
        metadata: {
          ...metadata,
          "current-snapshot-id": state.changed ? "1" : snapshot,
        },
      });
    }
    const query = JSON.parse(options.body).query;
    if (query.startsWith("EXPLAIN"))
      return Response.json({
        success: true,
        result: {
          schema: [],
          rows: [{ plan: { engine: "BASIN_SQL" } }],
          metrics: { bytes_scanned: 0 },
        },
      });
    if (state.changedDuring) state.changed = true;
    if (query.includes("AS exact_sum"))
      return Response.json({
        success: true,
        result: {
          schema: [],
          rows: [{ exact_sum: "9007199254740993.000000000000000002" }],
          metrics: { bytes_scanned: 100 },
        },
      });
    if (query.includes("precision_canary"))
      return Response.json({
        success: true,
        result: {
          schema: [],
          rows: [
            {
              exact_decimal: state.numericValue
                ? 42
                : "9007199254740993.000000000000000001",
              nullable_value: null,
            },
          ],
          metrics: { bytes_scanned: 100 },
        },
      });
    const row = {
      metric_id: null,
      mapping_version: null,
      period_start: null,
      currency: null,
      basis: null,
      availability_policy: null,
      source_observation_id: null,
      cik: "0001477333",
      entity_id: "sec:cik:0001477333",
      period_end: null,
      value_decimal: null,
      available_at: null,
      concept: null,
      unit: null,
      accession: null,
      member_sha256: null,
    };
    return Response.json({
      success: true,
      result: {
        schema: [],
        rows: state.tooMany ? [row, row] : [row],
        metrics: { bytes_scanned: 100 },
      },
    });
  };
  return {
    env,
    cache,
    state,
    fetcher,
    calls,
    get reads() {
      return reads;
    },
  };
}
const req = (q = "", principal = "private-owner") =>
  new Request("https://private.internal/v1/datasets/financial-query" + q, {
    headers: { "x-zion-principal": principal },
  });
function install(t, f) {
  const before = globalThis.fetch;
  globalThis.fetch = f.fetcher;
  t.after(() => (globalThis.fetch = before));
}
test("public fixed query preserves null and precise strings and persists scoped results", async (t) => {
  const f = await fixture();
  install(t, f);
  let r = await handleFinancialQuery(
    req("?template=precision_null_canary_v1"),
    f.env,
  );
  assert.equal(r.status, 200);
  let b = await r.json();
  assert.equal(
    b.data.rows[0].exact_decimal,
    "9007199254740993.000000000000000001",
  );
  assert.equal(b.data.rows[0].nullable_value, null);
  assert.equal(b.cache, "MISS");
  r = await handleFinancialQuery(
    req("?template=precision_null_canary_v1"),
    f.env,
  );
  b = await r.json();
  assert.equal(b.cache, "HIT");
  assert.equal(typeof b.plan.limits.max_rows, "number");
  assert(
    ![...f.cache.values()].some((x) =>
      JSON.stringify(x).includes("do-not-log-this-secret"),
    ),
  );
});
test("missing source facts are coverage rows, never financial zero or invented observations", async (t) => {
  const f = await fixture();
  install(t, f);
  const b = await (await handleFinancialQuery(req(), f.env)).json();
  assert.equal(
    b.data.rows[0].record_kind,
    "ENTITY_COVERAGE_WITH_NO_ELIGIBLE_PILOT_FACT",
  );
  assert.equal(b.data.rows[0].value_decimal, null);
});
test("unauthorized principal fails before policy or engine reads", async (t) => {
  const f = await fixture();
  install(t, f);
  assert.equal(
    (await handleFinancialQuery(req("", "other-user"), f.env)).status,
    403,
  );
  assert.equal(f.reads, 0);
  assert.equal(f.calls.length, 0);
});
test("SQL injection, unauthorized datasets, identifiers and historical ticker links fail closed", async (t) => {
  const f = await fixture();
  install(t, f);
  for (const [q, status] of [
    ["?sql=select", 400],
    ["?template=wrds_private", 403],
    ["?cik=bad", 400],
    ["?cik=0000000001", 403],
    ["?identifier_kind=ticker", 422],
    ["?recipient=llm", 403],
    ["?as_of=2025-02-30T00:00:00Z", 400],
  ])
    assert.equal((await handleFinancialQuery(req(q), f.env)).status, status);
  assert.equal(f.calls.length, 0);
});
test("source-version changes are rejected even when a cached result exists", async (t) => {
  const f = await fixture();
  install(t, f);
  assert.equal((await handleFinancialQuery(req(), f.env)).status, 200);
  f.state.changed = true;
  assert.equal((await handleFinancialQuery(req(), f.env)).status, 409);
});
test("numeric precision substitution and output truncation are rejected", async (t) => {
  const f = await fixture();
  install(t, f);
  f.state.numericValue = true;
  assert.equal(
    (
      await handleFinancialQuery(
        req("?template=precision_null_canary_v1"),
        f.env,
      )
    ).status,
    503,
  );
  f.state.tooMany = true;
  assert.equal(
    (await handleFinancialQuery(req("?limit=1"), f.env)).status,
    413,
  );
});
test("lossless metadata parsing preserves 64-bit snapshot identifiers", () => {
  assert.equal(
    exactParse('{"current-snapshot-id":9007199254740993123}')[
      "current-snapshot-id"
    ],
    "9007199254740993123",
  );
});
test("restricted public entrypoint cannot invoke SQL or receive a plan", async (t) => {
  const { handle } = await import("../../deploy/datasets/src/service.mjs");
  const f = await fixture();
  install(t, f);
  assert.equal((await handle(req(), f.env, true)).status, 404);
  assert.equal(f.calls.length, 0);
});
test("EXPLAIN returns a structured plan without fabricating observation annotations", async (t) => {
  const f = await fixture();
  install(t, f);
  const r = await handleFinancialQuery(req("?explain=true"), f.env);
  assert.equal(r.status, 200);
  const b = await r.json();
  assert.equal(b.plan.explain_only, true);
  assert.equal(b.data.rows[0].plan.engine, "BASIN_SQL");
  assert(!("financial_value_status" in b.data.rows[0]));
  assert.equal(f.cache.size, 0);
});
test("source mutation during engine execution rejects the result before caching", async (t) => {
  const f = await fixture();
  install(t, f);
  f.state.changedDuring = true;
  assert.equal((await handleFinancialQuery(req(), f.env)).status, 409);
  assert.equal(f.cache.size, 0);
});
test("derived decimal arithmetic canary remains explicitly synthetic and exact", async (t) => {
  const f = await fixture();
  install(t, f);
  const r = await handleFinancialQuery(
    req("?template=precision_arithmetic_canary_v1"),
    f.env,
  );
  assert.equal(r.status, 200);
  const b = await r.json();
  assert.equal(b.data.rows[0].exact_sum, "9007199254740993.000000000000000002");
  assert.equal(b.plan.semantics.source_layer, "OWNED_SYNTHETIC_TEST_ONLY");
  assert(!("financial_value_status" in b.data.rows[0]));
});

test("caller deadline includes stalled R2 policy reads", async (t) => {
  const f = await fixture();
  f.env.FINANCIAL_DATA.get = async () => new Promise(() => {});
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const pending = handleFinancialQuery(new Request("https://private/v1/datasets/financial-query", { headers: { "x-zion-principal": "private-owner" } }), f.env);
  t.mock.timers.tick(15001);
  const response = await pending;
  assert.equal(response.status, 504);
  assert.equal((await response.json()).error.code, "QUERY_TIMEOUT");
});


test("Catalog snapshot-list ordering does not change version identity", async () => {
  const a = { "current-snapshot-id": "2", snapshots: [{ "snapshot-id": "2", summary: { "total-records": "2" } }, { "snapshot-id": "1" }] };
  const b = { ...a, snapshots: [...a.snapshots].reverse() };
  assert.equal(await digest(JSON.stringify(canonicalMetadata(a))), await digest(JSON.stringify(canonicalMetadata(b))));
  b.snapshots[0] = { "snapshot-id": "1", summary: { "total-records": "99" } };
  assert.notEqual(await digest(JSON.stringify(canonicalMetadata(a))), await digest(JSON.stringify(canonicalMetadata(b))));
});
