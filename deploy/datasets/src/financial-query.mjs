// Closed, public-data-only Basin pilot. Never accepts user SQL, table names or credentials.
import { parse, isLosslessNumber } from "lossless-json";
import { digest, NativeFailure } from "./native.mjs";

const ACCOUNT = "51b88c6a6ef833b3c2ff46e98d5d9356";
const BUCKET = "financial-system-datasets";
const POLICY_PREFIX = "gold/unified-foundation/policies/";
const RESPONSE_BYTES = 262144;
const fail = (code, status = 400) => {
  throw new NativeFailure(code, status);
};
function normalize(x) {
  if (isLosslessNumber(x)) return x.toString();
  if (Array.isArray(x)) return x.map(normalize);
  if (x && typeof x === "object")
    return Object.fromEntries(
      Object.entries(x)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([k, v]) => [k, normalize(v)]),
    );
  return x;
}
export const exactParse = (raw) => normalize(parse(raw));
export function canonicalMetadata(metadata) {
  const copy = normalize(metadata);
  // Catalog does not guarantee snapshot-list ordering; identity is snapshot-id.
  if (Array.isArray(copy.snapshots))
    copy.snapshots.sort((a, b) => String(a["snapshot-id"]).localeCompare(String(b["snapshot-id"])));
  return copy;
}
const json = (value, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "private, no-store",
      "x-content-type-options": "nosniff",
    },
  });
function remaining(deadline) {
  const ms = deadline - Date.now();
  if (ms <= 0) fail("QUERY_TIMEOUT", 504);
  return ms;
}
async function bounded(response, maximum = RESPONSE_BYTES) {
  if (!response.ok) {
    await response.body?.cancel();
    fail("BASIN_UPSTREAM_UNAVAILABLE", 503);
  }
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maximum) {
        await reader.cancel();
        fail("RESPONSE_BUDGET_EXCEEDED", 413);
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const out = new Uint8Array(size);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.length;
  }
  const parsed = parse(new TextDecoder().decode(out));
  const result = normalize(parsed);
  if (Array.isArray(parsed?.result?.rows))
    result.__numeric_result_cells = parsed.result.rows.some((row) =>
      Object.values(row).some(isLosslessNumber),
    );
  return result;
}
async function policy(env) {
  if (
    !env.BASIN_POLICY_KEY?.startsWith(POLICY_PREFIX) ||
    env.BASIN_POLICY_KEY.split("/").some((x) => x === "." || x === "..") ||
    !/^[a-f0-9]{64}$/.test(env.BASIN_POLICY_SHA256 ?? "")
  )
    fail("POLICY_UNCONFIGURED", 503);
  const object = await env.FINANCIAL_DATA.get(env.BASIN_POLICY_KEY);
  if (!object || object.size > RESPONSE_BYTES) fail("POLICY_UNAVAILABLE", 503);
  const raw = await object.text();
  if ((await digest(raw)) !== env.BASIN_POLICY_SHA256)
    fail("POLICY_INTEGRITY_FAILURE", 503);
  const p = JSON.parse(raw);
  if (
    p.schema_version !== "zion-financial-query-policy-v1" ||
    p.account_id !== ACCOUNT ||
    p.bucket !== BUCKET ||
    p.allowed_principal !== "private-owner" ||
    p.licensed_datasets_allowed !== false ||
    p.promotion_allowed !== false ||
    !/^zion_uqf_pilot_[a-f0-9]{12}$/.test(p.namespace) ||
    !/^[a-f0-9-]{36}$/.test(p.catalog_prefix) ||
    !Array.isArray(p.allowed_ciks) ||
    p.allowed_ciks.some((c) => !/^\d{10}$/.test(c)) ||
    p.allowed_ciks.length < 1 ||
    p.allowed_ciks.length > 10
  )
    fail("POLICY_SCOPE_INVALID", 503);
  if (
    !Array.isArray(p.tables) ||
    p.tables.length !== 3 ||
    p.tables.some(
      (t) =>
        t.namespace !== p.namespace ||
        !["issuer_anchor", "sec_concept_facts", "precision_canary"].includes(
          t.name,
        ) ||
        !["PUBLIC_SEC_ONLY", "OWNED_SYNTHETIC_TEST_ONLY"].includes(t.rights) ||
        !/^[0-9]+$/.test(t.snapshot_id) ||
        !/^[a-f0-9]{64}$/.test(t.metadata_canonical_sha256) ||
        !Number.isSafeInteger(t.files_bytes_declared) ||
        t.files_bytes_declared < 0,
    )
  )
    fail("POLICY_SCOPE_INVALID", 503);
  if (
    new Set(p.tables.map((t) => t.name)).size !== 3 ||
    p.tables.reduce((n, t) => n + t.files_bytes_declared, 0) > 1048576
  )
    fail("SOURCE_SCAN_BUDGET_EXCEEDED", 413);
  return p;
}
export function compilePlan(p, request) {
  const u = new URL(request.url),
    q = u.searchParams;
  for (const k of q.keys())
    if (
      ![
        "template",
        "cik",
        "as_of",
        "limit",
        "explain",
        "identifier_kind",
        "recipient",
      ].includes(k) ||
      q.getAll(k).length !== 1
    )
      fail("INVALID_PARAMETER");
  if (q.has("recipient") && q.get("recipient") !== "human_research")
    fail("RECIPIENT_NOT_PERMITTED", 403);
  if (q.has("identifier_kind") && q.get("identifier_kind") !== "cik")
    fail("HISTORICAL_IDENTITY_NOT_ESTABLISHED", 422);
  const template = q.get("template") ?? "sec_concept_history_v1";
  if (
    ![
      "sec_concept_history_v1",
      "precision_null_canary_v1",
      "precision_arithmetic_canary_v1",
    ].includes(template)
  )
    fail("TEMPLATE_NOT_PERMITTED", 403);
  const limitText = q.get("limit") ?? "100";
  if (
    !/^\d{1,3}$/.test(limitText) ||
    Number(limitText) < 1 ||
    Number(limitText) > 100
  )
    fail("ROW_BUDGET_EXCEEDED", 400);
  const limit = Number(limitText);
  let cik = q.get("cik");
  if (cik && !/^\d{10}$/.test(cik)) fail("INVALID_IDENTIFIER");
  if (cik && !p.allowed_ciks.includes(cik))
    fail("IDENTIFIER_NOT_PERMITTED", 403);
  const asOf = q.get("as_of") ?? "2026-01-01T00:00:00Z";
  if (
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(asOf) ||
    !Number.isFinite(Date.parse(asOf)) ||
    new Date(asOf).toISOString() !== asOf.replace("Z", ".000Z") ||
    Date.parse(asOf) > Date.now()
  )
    fail("INVALID_AVAILABILITY_CUTOFF");
  if (
    template === "sec_concept_history_v1" &&
    p.source_as_of &&
    asOf > p.source_as_of
  )
    fail("PILOT_SOURCE_WINDOW_EXCEEDED", 422);
  const explain = q.get("explain") ?? "false";
  if (!["true", "false"].includes(explain)) fail("INVALID_PARAMETER");
  const selected =
    template !== "sec_concept_history_v1"
      ? ["precision_canary"]
      : ["issuer_anchor", "sec_concept_facts"];
  const tables = p.tables.filter((t) => selected.includes(t.name));
  const sql =
    template === "precision_arithmetic_canary_v1"
      ? `SELECT CAST(CAST(exact_decimal AS DECIMAL(38,18)) + CAST('0.000000000000000001' AS DECIMAL(38,18)) AS VARCHAR) AS exact_sum FROM ${p.namespace}.precision_canary LIMIT ${limit + 1}`
      : template === "precision_null_canary_v1"
        ? `SELECT exact_decimal, nullable_value FROM ${p.namespace}.precision_canary LIMIT ${limit + 1}`
        : `WITH eligible AS (SELECT f.*, ROW_NUMBER() OVER (PARTITION BY cik, concept, unit, period_start, period_end ORDER BY available_at DESC, accession DESC, source_observation_id DESC) AS revision_order FROM ${p.namespace}.sec_concept_facts f WHERE available_at <= '${asOf}'), selected AS (SELECT * FROM eligible WHERE revision_order = 1) SELECT i.cik, i.entity_id, f.metric AS metric_id, f.mapping_version, f.period_start, f.period_end, f.value_decimal, f.unit, f.currency, f.basis, f.available_at, f.availability_policy, f.concept, f.accession, f.member_sha256, f.source_observation_id FROM ${p.namespace}.issuer_anchor i LEFT JOIN selected f ON i.cik = f.cik WHERE i.cik IN (${(cik ? [cik] : p.allowed_ciks).map((c) => "'" + c + "'").join(",")}) ORDER BY i.cik, f.period_end LIMIT ${limit + 1}`;
  return {
    schema_version: "zion-financial-query-plan-v1",
    engine: "BASIN_SQL",
    template,
    sql,
    explain_only: explain === "true",
    as_of: asOf,
    principal: "private-owner",
    rights: "PUBLIC_SEC_OR_OWNED_TEST_ONLY",
    licensed_data_allowed: false,
    tables,
    limits: {
      max_rows: limit,
      max_source_files_bytes: 1048576,
      max_response_bytes: RESPONSE_BYTES,
      client_timeout_ms: 15000,
      timeout_is_engine_cancellation: false,
    },
    semantics: {
      identity: "ISSUER_CIK_ANCHOR;NO_HISTORICAL_TICKER_LINK",
      metric:
        "ONE_EXPLICIT_NATIVE_US_GAAP_CONCEPT;NOT_TOTAL_REVENUE_CERTIFICATION",
      null: "NO_ELIGIBLE_SOURCE_OBSERVATION;NOT_ZERO",
      source_layer:
        template === "sec_concept_history_v1"
          ? "NORMALIZED_RESEARCH_SOURCE_UNVALIDATED"
          : "OWNED_SYNTHETIC_TEST_ONLY",
    },
    availability_policy: "DATE_ONLY_NEXT_DAY_ET_V1",
    source_period_window: p.source_period_window ?? null,
    maximum_supported_source_as_of: p.source_as_of ?? null,
    temporal_universe_qualification:
      "REGISTERED_PILOT_CONTEXTS_ONLY;NOT_A_HISTORICAL_SECURITY_UNIVERSE",
  };
}
async function versionCheck(p, plan, token, deadline) {
  if (typeof AbortSignal.timeout !== "function")
    fail("RUNTIME_TIMEOUT_UNSUPPORTED", 503);
  for (const t of plan.tables) {
    const url = `https://catalog.cloudflarestorage.com/${ACCOUNT}/${BUCKET}/v1/${p.catalog_prefix}/namespaces/${p.namespace}/tables/${t.name}`;
    const loaded = await bounded(
      await fetch(url, {
        headers: {
          authorization: "Bearer " + token,
          "user-agent": "zion-foundation-query/1",
        },
        redirect: "manual",
        signal: AbortSignal.timeout(remaining(deadline)),
      }).catch(() => fail("CATALOG_FETCH_FAILED", 503)),
    ).catch((e) => {
      if (e instanceof NativeFailure) throw e;
      fail("CATALOG_DECODE_FAILED", 503);
    });
    const snapshot = loaded.metadata?.snapshots?.find(
      (s) => String(s["snapshot-id"]) === t.snapshot_id,
    );
    const observed = snapshot?.summary?.["total-files-size"];
    if (!/^\d+$/.test(observed ?? "")) fail("SOURCE_BUDGET_UNVERIFIABLE", 503);
    if (BigInt(observed) !== BigInt(t.files_bytes_declared))
      fail("SOURCE_BUDGET_MISMATCH", 409);
    if (
      loaded["metadata-location"] !== t.metadata_location ||
      String(loaded.metadata?.["current-snapshot-id"]) !== t.snapshot_id ||
      (await digest(JSON.stringify(canonicalMetadata(loaded.metadata)))) !==
        t.metadata_canonical_sha256
    )
      fail("SOURCE_VERSION_CHANGED", 409);
  }
}
async function execute(request, env) {
  if (request.method !== "GET") fail("READ_ONLY", 405);
  if (request.headers.get("x-zion-principal") !== "private-owner")
    fail("PRINCIPAL_NOT_PERMITTED", 403);
  if (!env.BASIN_SQL_TOKEN) fail("BASIN_CREDENTIAL_UNCONFIGURED", 503);
  const deadline = Date.now() + 15000;
  const p = await policy(env),
    plan = compilePlan(p, request);
  plan.plan_id = await digest(JSON.stringify(plan));
  await versionCheck(p, plan, env.BASIN_SQL_TOKEN, deadline).catch((e) => {
    if (e instanceof NativeFailure) throw e;
    fail("CATALOG_RUNTIME_FAILURE", 503);
  });
  const prefix = "gold/unified-query-cache/v1/",
    key =
      prefix +
      (await digest(
        JSON.stringify({
          policy: env.BASIN_POLICY_SHA256,
          principal: plan.principal,
          plan: plan.plan_id,
        }),
      )) +
      ".json";
  if (!plan.explain_only) {
    const cached = await env.FINANCIAL_DATA.get(key);
    if (cached) {
      if (cached.size > RESPONSE_BYTES) fail("CACHE_INTEGRITY_FAILURE", 503);
      const raw = await cached.text();
      if ((await digest(raw)) !== cached.customMetadata?.sha256)
        fail("CACHE_INTEGRITY_FAILURE", 503);
      const value = JSON.parse(raw);
      if (value.plan?.plan_id !== plan.plan_id)
        fail("CACHE_SCOPE_MISMATCH", 503);
      await versionCheck(p, plan, env.BASIN_SQL_TOKEN, deadline).catch((e) => {
    if (e instanceof NativeFailure) throw e;
    fail("CATALOG_RUNTIME_FAILURE", 503);
  });
      return { ...value, cache: "HIT" };
    }
  }
  const response = await bounded(
    await fetch(
      `https://api.sql.cloudflarestorage.com/api/v1/accounts/${ACCOUNT}/basin-sql/query/${BUCKET}`,
      {
        method: "POST",
        headers: {
          authorization: "Bearer " + env.BASIN_SQL_TOKEN,
          "content-type": "application/json",
          "user-agent": "zion-foundation-query/1",
        },
        body: JSON.stringify({
          query: (plan.explain_only ? "EXPLAIN FORMAT JSON " : "") + plan.sql,
        }),
        redirect: "manual",
        signal: AbortSignal.timeout(remaining(deadline)),
      },
    ),
  );
  if (
    response.success !== true ||
    !response.result ||
    !Array.isArray(response.result.rows)
  )
    fail("BASIN_RESULT_INVALID", 503);
  const r = response.result;
  if (!plan.explain_only && r.rows.length > plan.limits.max_rows)
    fail("ROW_LIMIT_EXCEEDED", 413);
  if (
    r.metrics?.bytes_scanned === undefined ||
    BigInt(r.metrics.bytes_scanned) > 1048576n
  )
    fail("SCAN_METRICS_UNAVAILABLE_OR_OVER_BUDGET", 413);
  if (!plan.explain_only && response.__numeric_result_cells)
    fail("RESULT_SCHEMA_OR_PRECISION_INVALID", 503);
  if (!plan.explain_only) {
    const expected =
      plan.template === "precision_arithmetic_canary_v1"
        ? ["exact_sum"]
        : plan.template === "precision_null_canary_v1"
          ? ["exact_decimal", "nullable_value"]
          : [
              "cik",
              "entity_id",
              "metric_id",
              "mapping_version",
              "period_start",
              "currency",
              "basis",
              "availability_policy",
              "source_observation_id",
              "period_end",
              "value_decimal",
              "available_at",
              "concept",
              "unit",
              "accession",
              "member_sha256",
            ];
    if (
      r.rows.some(
        (row) =>
          Object.keys(row).sort().join(",") !==
            [...expected].sort().join(",") ||
          Object.values(row).some((v) => v !== null && typeof v !== "string"),
      )
    )
      fail("RESULT_SCHEMA_OR_PRECISION_INVALID", 503);
  }
  await versionCheck(p, plan, env.BASIN_SQL_TOKEN, deadline).catch((e) => {
    if (e instanceof NativeFailure) throw e;
    fail("CATALOG_RUNTIME_FAILURE", 503);
  });
  const result = {
    schema_version: "zion-financial-query-v1",
    plan,
    provenance: {
      policy_sha256: env.BASIN_POLICY_SHA256,
      source_sha256: p.source_sha256,
      source_versions_checked_before_and_after: true,
      licensed_sources_used: false,
      canonical_promotion: false,
    },
    data: {
      schema: [
        ...r.schema,
        ...(plan.template === "sec_concept_history_v1" && !plan.explain_only
          ? [
              {
                name: "record_kind",
                descriptor: { type: { name: "string" }, nullable: false },
                origin: "ADAPTER_COVERAGE_ANNOTATION",
              },
              {
                name: "financial_value_status",
                descriptor: { type: { name: "string" }, nullable: false },
                origin: "ADAPTER_COVERAGE_ANNOTATION",
              },
            ]
          : []),
      ],
      engine_schema: r.schema,
      rows: r.rows.map((row) =>
        plan.explain_only || plan.template !== "sec_concept_history_v1"
          ? row
          : {
              ...row,
              record_kind:
                row.period_end === null
                  ? "ENTITY_COVERAGE_WITH_NO_ELIGIBLE_PILOT_FACT"
                  : "NORMALIZED_SOURCE_OBSERVATION",
              financial_value_status:
                row.value_decimal === null
                  ? "UNAVAILABLE_IN_PERMITTED_PILOT_NOT_ZERO"
                  : "SOURCE_REPORTED",
            },
      ),
    },
    execution_metrics: r.metrics,
    cache: "MISS",
  };
  remaining(deadline);
  const raw = JSON.stringify(result);
  if (new TextEncoder().encode(raw).length > RESPONSE_BYTES)
    fail("RESPONSE_BUDGET_EXCEEDED", 413);
  if (!plan.explain_only)
    await env.FINANCIAL_DATA.put(key, raw, {
      httpMetadata: { contentType: "application/json" },
      customMetadata: { sha256: await digest(raw) },
      onlyIf: { etagDoesNotMatch: "*" },
    });
  return result;
}
export async function handleFinancialQuery(request, env) {
  let timer;
  try {
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new NativeFailure("QUERY_TIMEOUT", 504)),
        15000,
      );
    });
    // Bounds the caller response, including R2 waits. Does not cancel upstream work.
    return json(await Promise.race([execute(request, env), timeout]));
  } catch (e) {
    return json(
      {
        error: {
          code:
            e instanceof NativeFailure ? e.code : "FINANCIAL_QUERY_UNAVAILABLE",
          retryable: !(e instanceof NativeFailure) || e.status >= 500,
        },
      },
      e instanceof NativeFailure ? e.status : 503,
    );
  } finally {
    clearTimeout(timer);
  }
}
