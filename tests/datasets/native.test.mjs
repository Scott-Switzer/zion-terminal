import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { handleNative } from "../../deploy/datasets/src/native.mjs";

class Bucket {
  constructor(raw) {
    this.raw = raw;
    this.etag = "v1";
    this.calls = [];
    this.cache = new Map();
  }
  async head(key) {
    return {
      key,
      size: this.raw.length,
      etag: this.etag,
      httpEtag: `"${this.etag}"`,
    };
  }
  async get(key, opts) {
    if (!opts) return this.cache.get(key) ?? null;
    this.calls.push(opts);
    if (opts.onlyIf.etagMatches !== this.etag) return null;
    const r = opts.range;
    const raw = this.raw.slice(r.offset, r.offset + r.length);
    return {
      etag: this.etag,
      arrayBuffer: async () =>
        raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.length),
    };
  }
  async put(key, raw, opts) {
    this.cache.set(key, {
      size: raw.length,
      text: async () => raw,
      customMetadata: opts.customMetadata,
    });
  }
}
async function fixture(codec = "snappy") {
  const raw = await readFile(
    new URL(`./fixtures/native-${codec}.parquet`, import.meta.url),
  );
  const source = new Bucket(raw);
  const cache = new Bucket(Buffer.alloc(0));
  return { source, env: { RESEARCH_DATA: source, FINANCIAL_DATA: cache } };
}
const req = (path) =>
  new Request("https://private.internal/v1/datasets/research/" + path);
for (const codec of ["snappy", "gzip", "zstd"])
  test(`native ${codec} projection preserves decimal, int64 and timestamp precision`, async () => {
    const { env } = await fixture(codec);
    const response = await handleNative(
      req("rows?path=table.parquet&columns=id,amount,timestamp&limit=2"),
      env,
    );
    assert.equal(response.status, 200);
    const r = await response.json();
    assert.equal(r.data.rows[0].id, "9007199254740993");
    assert.equal(r.data.rows[0].amount, "123456789012345.123456");
    assert.equal(r.data.rows[1].amount, "-0.000001");
    assert.equal(r.data.rows[0].timestamp.epoch, "1");
    assert(r.next_cursor);
    assert.equal(r.cache, "MISS");
    const again = await (
      await handleNative(
        req("rows?path=table.parquet&columns=id,amount,timestamp&limit=2"),
        env,
      )
    ).json();
    assert.equal(again.cache, "HIT");
  });
test("footer schema exposes all native fields and distinguishes declaration from normalization", async () => {
  const { env } = await fixture();
  const r = await (
    await handleNative(req("schema?path=table.parquet"), env)
  ).json();
  assert.equal(r.data.row_count, 4);
  assert.equal(r.data.columns.length, 5);
  assert.equal(r.quality, "SOURCE_UNVALIDATED");
});
test("continuation pins source version and scope", async () => {
  const { env, source } = await fixture();
  const r = await (
    await handleNative(req("rows?path=table.parquet&columns=id&limit=2"), env)
  ).json();
  const next =
    "rows?path=table.parquet&columns=id&limit=2&cursor=" +
    encodeURIComponent(r.next_cursor);
  const n = await (await handleNative(req(next), env)).json();
  assert.deepEqual(
    n.data.rows.map((x) => x.id),
    ["3", "4"],
  );
  source.etag = "v2";
  assert.equal((await handleNative(req(next), env)).status, 412);
  assert.equal(
    (
      await handleNative(
        req(next.replace("table.parquet", "other.parquet")),
        env,
      )
    ).status,
    400,
  );
});
test("rejects unsafe projections, traversal, repeated parameters and arbitrary SQL", async () => {
  const { env, source } = await fixture();
  for (const p of [
    "rows?path=../x",
    "rows?path=x&sql=select",
    "rows?path=x&path=y",
    "rows?path=x&limit=999999",
    "rows?path=x&columns=unknown",
  ])
    assert.equal((await handleNative(req(p), env)).status, 400);
  assert.equal(
    (
      await handleNative(
        new Request(
          "https://private.internal/v1/datasets/research/rows?path=x",
          { method: "POST" },
        ),
        env,
      )
    ).status,
    405,
  );
});
test("cached result digest mismatch fails closed", async () => {
  const { env } = await fixture();
  await handleNative(req("rows?path=table.parquet&columns=id"), env);
  const cached = [...env.FINANCIAL_DATA.cache.values()][0];
  cached.text = async () => "{}";
  assert.equal(
    (await handleNative(req("rows?path=table.parquet&columns=id"), env)).status,
    503,
  );
});
test("range and decoded budgets fail explicitly rather than returning partial rows", async () => {
  const { sourceFile } = await import("../../deploy/datasets/src/native.mjs");
  const file = sourceFile({}, "huge.parquet", { size: 100000000, etag: "v1" });
  await assert.rejects(
    file.slice(0, 9 * 1048576),
    (e) => e.code === "SOURCE_READ_BUDGET_EXCEEDED",
  );
  const { env, source } = await fixture();
  source.get = async () => null;
  assert.equal(
    (await handleNative(req("rows?path=table.parquet&columns=id"), env)).status,
    412,
  );
});
test("nulls and non-finite source floats are explicit", async () => {
  const { env } = await fixture();
  const r = await (
    await handleNative(
      req("rows?path=table.parquet&columns=amount,missing&limit=4"),
      env,
    )
  ).json();
  assert.equal(r.data.rows[2].amount, null);
  assert.equal(r.data.rows[2].missing, null);
  assert.deepEqual(r.data.rows[0].missing, { non_finite: "NaN" });
  assert.deepEqual(r.data.rows[1].missing, { non_finite: "Infinity" });
});
test("restricted public catalog cannot access native rows or schemas", async () => {
  const { handle } = await import("../../deploy/datasets/src/service.mjs");
  const { env } = await fixture();
  assert.equal(
    (await handle(req("schema?path=table.parquet"), env, true)).status,
    404,
  );
  assert.equal(
    (await handle(req("rows?path=table.parquet"), env, true)).status,
    404,
  );
});
test("gzip expanded length mismatch is rejected before values can be decoded", async () => {
  const { gzipSync } = await import("node:zlib");
  const { decompressGzip } = await import(
    "../../deploy/datasets/src/native.mjs"
  );
  const raw = gzipSync(Buffer.from("hello"));
  assert.throws(
    () => decompressGzip(raw, 1),
    (e) => e.code === "DECOMPRESSED_LENGTH_MISMATCH",
  );
  assert.throws(
    () => decompressGzip(raw, 6),
    (e) => e.code === "DECOMPRESSED_LENGTH_MISMATCH",
  );
  assert.equal(new TextDecoder().decode(decompressGzip(raw, 5)), "hello");
});
test("zstd expanded length mismatch and oversized windows are rejected", async () => {
  const { decompressZstd } = await import(
    "../../deploy/datasets/src/native.mjs"
  );
  const raw = Uint8Array.from([
    40, 181, 47, 253, 32, 5, 41, 0, 0, 104, 101, 108, 108, 111,
  ]);
  assert.throws(
    () => decompressZstd(raw, 1),
    (e) => e.code === "DECOMPRESSED_LENGTH_MISMATCH",
  );
  assert.throws(
    () => decompressZstd(raw, 6),
    (e) => e.code === "DECOMPRESSED_LENGTH_MISMATCH",
  );
  assert.equal(new TextDecoder().decode(decompressZstd(raw, 5)), "hello");
  const huge = Uint8Array.from([40, 181, 47, 253, 0, 255, 1, 0, 0]);
  assert.throws(
    () => decompressZstd(huge, 1),
    (e) => e.code === "DECODE_BUDGET_EXCEEDED",
  );
});
