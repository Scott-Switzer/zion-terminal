// Native source queries: no financial mapping, arbitrary SQL, provider calls or local scans.
import {
  parquetMetadataAsync,
  parquetReadObjects,
  snappyUncompress,
} from "hyparquet";
import { Gunzip } from "fflate";
import { Decompress as ZstdDecompress } from "fzstd";

const STORES = {
  financial: "FINANCIAL_DATA",
  research: "RESEARCH_DATA",
  filings: "SEC_CORPUS",
  lakehouse: "RESEARCH_LAKEHOUSE",
};
const MAX_READ = 8 * 1048576,
  MAX_DECODE = 16 * 1048576,
  MAX_RESPONSE = 1048576;
const VERSION = "native-parquet-v2";
export class NativeFailure extends Error {
  constructor(code, status = 400) {
    super(code);
    this.code = code;
    this.status = status;
  }
}
const fail = (code, status) => {
  throw new NativeFailure(code, status);
};
export const encode = (value) =>
  JSON.stringify(value, (_, v) => (typeof v === "bigint" ? v.toString() : v));
export async function digest(raw) {
  return Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(raw)),
    ),
    (x) => x.toString(16).padStart(2, "0"),
  ).join("");
}
function json(value, status = 200) {
  return new Response(encode(value), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "private, no-store",
      "x-content-type-options": "nosniff",
    },
  });
}
const cursorEncode = (x) => {
  const token = btoa(encodeURIComponent(encode(x)));
  if (token.length > 16384) fail("CURSOR_BUDGET_EXCEEDED", 413);
  return token;
};
function cursorDecode(x) {
  try {
    if (x.length > 16384) throw Error();
    return JSON.parse(decodeURIComponent(atob(x)));
  } catch {
    fail("INVALID_CURSOR");
  }
}
function integer(value, fallback, max = Number.MAX_SAFE_INTEGER) {
  if (value === null) return fallback;
  if (!/^\d{1,16}$/.test(value)) fail("INVALID_INTEGER");
  const n = Number(value);
  if (!Number.isSafeInteger(n) || n > max) fail("INVALID_INTEGER");
  return n;
}
function keyPath(value) {
  if (
    !value ||
    value.length > 1024 ||
    value.startsWith("/") ||
    /[\\\x00-\x1f\x7f]/.test(value) ||
    value.split("/").some((p) => p === "." || p === "..")
  )
    fail("INVALID_PATH");
  return value;
}
function parameters(url, allowed) {
  for (const k of url.searchParams.keys())
    if (!allowed.includes(k) || url.searchParams.getAll(k).length !== 1)
      fail("INVALID_PARAMETER");
  return url.searchParams;
}
export function sourceFile(bucket, path, head) {
  let bytes = 0;
  return {
    byteLength: head.size,
    get bytesRead() {
      return bytes;
    },
    async slice(start, end = head.size) {
      if (start < 0) start += head.size;
      if (end < 0) end += head.size;
      const n = end - start;
      if (
        !Number.isSafeInteger(start) ||
        !Number.isSafeInteger(end) ||
        start < 0 ||
        end > head.size ||
        n < 0 ||
        n > MAX_READ ||
        bytes + n > MAX_READ
      )
        fail("SOURCE_READ_BUDGET_EXCEEDED", 413);
      bytes += n;
      const output = new Uint8Array(n);
      for (let pos = 0; pos < n; pos += 1048576) {
        const length = Math.min(1048576, n - pos);
        const o = await bucket.get(path, {
          range: { offset: start + pos, length },
          onlyIf: { etagMatches: head.etag },
        });
        if (!o || o.etag !== head.etag) fail("SOURCE_VERSION_CHANGED", 412);
        const raw = new Uint8Array(await o.arrayBuffer());
        if (raw.byteLength !== length) fail("SOURCE_RANGE_INVALID", 503);
        output.set(raw, pos);
      }
      return output.buffer;
    },
  };
}
function expandedStream(input, length, Stream) {
  if (!Number.isSafeInteger(length) || length < 0 || length > MAX_DECODE)
    fail("DECODE_BUDGET_EXCEEDED", 413);
  const output = new Uint8Array(length);
  let written = 0;
  const stream = new Stream((chunk) => {
    if (written + chunk.length > length)
      fail("DECOMPRESSED_LENGTH_MISMATCH", 422);
    output.set(chunk, written);
    written += chunk.length;
  });
  for (let offset = 0; offset < input.length; offset += 1024)
    stream.push(
      input.subarray(offset, offset + 1024),
      offset + 1024 >= input.length,
    );
  if (written !== length) fail("DECOMPRESSED_LENGTH_MISMATCH", 422);
  return output;
}
export function decompressGzip(input, length) {
  return expandedStream(input, length, Gunzip);
}
function validateZstdFrames(input) {
  let pos = 0;
  while (pos < input.length) {
    if (
      pos + 5 > input.length ||
      input[pos] !== 0x28 ||
      input[pos + 1] !== 0xb5 ||
      input[pos + 2] !== 0x2f ||
      input[pos + 3] !== 0xfd
    )
      fail("ZSTD_FRAME_INVALID", 422);
    const descriptor = input[pos + 4],
      single = (descriptor >> 5) & 1;
    if (descriptor & 0x18) fail("ZSTD_FRAME_INVALID", 422);
    let next = pos + 5;
    let window = 0;
    if (!single) {
      const wd = input[next++];
      window = 2 ** (10 + (wd >> 3)) * (1 + (wd & 7) / 8);
    }
    const dictionary = descriptor & 3;
    next += dictionary === 3 ? 4 : dictionary;
    const flag = descriptor >> 6,
      sizeBytes = flag ? 1 << flag : single;
    if (next + sizeBytes > input.length) fail("ZSTD_FRAME_INVALID", 422);
    let content = 0n;
    for (let i = 0; i < sizeBytes; i++)
      content += BigInt(input[next + i]) << BigInt(8 * i);
    if (flag === 1) content += 256n;
    if (single) window = Number(content);
    if (window > MAX_DECODE || content > BigInt(MAX_DECODE))
      fail("DECODE_BUDGET_EXCEEDED", 413);
    next += sizeBytes;
    let last = false;
    while (!last) {
      if (next + 3 > input.length) fail("ZSTD_FRAME_INVALID", 422);
      const block =
        input[next] | (input[next + 1] << 8) | (input[next + 2] << 16);
      last = !!(block & 1);
      const type = (block >> 1) & 3;
      if (type === 3) fail("ZSTD_FRAME_INVALID", 422);
      next += 3 + (type === 1 ? 1 : block >> 3);
      if (next > input.length) fail("ZSTD_FRAME_INVALID", 422);
    }
    if (descriptor & 4) next += 4;
    if (next > input.length) fail("ZSTD_FRAME_INVALID", 422);
    pos = next;
  }
}
export function decompressZstd(input, length) {
  validateZstdFrames(input);
  return expandedStream(input, length, ZstdDecompress);
}
function decimal(value, scale) {
  if (value === null || value === undefined) return null;
  let n;
  if (value instanceof Uint8Array) {
    n = 0n;
    for (const byte of value) n = n * 256n + BigInt(byte);
    if (value.length && value[0] & 128) n -= 1n << BigInt(value.length * 8);
  } else n = BigInt(value);
  const negative = n < 0n;
  if (negative) n = -n;
  let s = n.toString().padStart(scale + 1, "0");
  if (scale) s = s.slice(0, -scale) + "." + s.slice(-scale);
  return (negative ? "-" : "") + s;
}
function nativeValue(value) {
  if (value === undefined || value === null) return null;
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "number" && !Number.isFinite(value))
    return { non_finite: String(value) };
  if (value instanceof Uint8Array)
    return {
      encoding: "base64",
      value: btoa(Array.from(value, (b) => String.fromCharCode(b)).join("")),
    };
  if (value instanceof Date) return value.toISOString();
  return value;
}
async function query(request, env) {
  if (request.method !== "GET") fail("READ_ONLY", 405);
  const url = new URL(request.url),
    match = /^\/v1\/datasets\/([a-z]+)\/(schema|rows)$/.exec(url.pathname);
  if (!match || !STORES[match[1]]) fail("NOT_FOUND", 404);
  const [, store, operation] = match,
    q = parameters(
      url,
      operation === "schema"
        ? ["path"]
        : ["path", "columns", "offset", "limit", "cursor"],
    );
  const path = keyPath(q.get("path")),
    bucket = env[STORES[store]];
  if (!bucket) fail("STORE_UNAVAILABLE", 503);
  const columns = q.has("columns") ? q.get("columns").split(",") : null;
  if (
    columns &&
    (!columns.length ||
      columns.length > 256 ||
      columns.some((x) => !x || x.length > 256) ||
      new Set(columns).size !== columns.length)
  )
    fail("INVALID_COLUMNS");
  let offset = integer(q.get("offset"), 0),
    limit = integer(q.get("limit"), 100, 1000);
  if (limit < 1) fail("INVALID_LIMIT");
  let cursor = null;
  if (q.has("cursor")) {
    cursor = cursorDecode(q.get("cursor"));
    if (
      q.has("offset") ||
      cursor.v !== VERSION ||
      cursor.store !== store ||
      cursor.path !== path ||
      encode(cursor.columns) !== encode(columns) ||
      !Number.isSafeInteger(cursor.offset) ||
      cursor.offset < 0 ||
      typeof cursor.etag !== "string"
    )
      fail("CURSOR_SCOPE_MISMATCH");
    offset = cursor.offset;
  }
  const head = await bucket.head(path);
  if (!head) fail("OBJECT_NOT_FOUND", 404);
  if (head.size < 8) fail("NOT_PARQUET", 422);
  if (cursor && cursor.etag !== head.etag) fail("SOURCE_VERSION_CHANGED", 412);
  const expected = request.headers.get("if-match");
  if (expected && expected !== head.httpEtag)
    fail("SOURCE_VERSION_CHANGED", 412);
  const cacheKey =
    "gold/native-query/v1/" +
    (await digest(
      encode({
        v: VERSION,
        store,
        path,
        etag: head.etag,
        operation,
        columns,
        offset,
        limit,
      }),
    )) +
    ".json";
  const cached = await env.FINANCIAL_DATA.get(cacheKey);
  if (cached) {
    if (cached.size > MAX_RESPONSE) fail("CACHE_INTEGRITY_FAILURE", 503);
    const raw = await cached.text();
    if ((await digest(raw)) !== cached.customMetadata?.sha256)
      fail("CACHE_INTEGRITY_FAILURE", 503);
    const value = JSON.parse(raw);
    if (value.source.etag !== head.etag) fail("CACHE_INTEGRITY_FAILURE", 503);
    return { ...value, cache: "HIT" };
  }
  const file = sourceFile(bucket, path, head);
  let metadata;
  try {
    metadata = await parquetMetadataAsync(file, { initialFetchSize: 8 });
  } catch (e) {
    if (e instanceof NativeFailure) throw e;
    fail("PARQUET_METADATA_UNAVAILABLE", 422);
  }
  const count = Number(metadata.num_rows);
  if (!Number.isSafeInteger(count))
    fail("ROW_COUNT_OUTSIDE_SUPPORTED_RANGE", 422);
  const fields = metadata.schema.slice(1);
  const schema = {
    schema_status: "PARQUET_FOOTER_DECODED",
    row_count_status: "FOOTER_DECLARED_NOT_FULL_SCAN",
    row_count: count,
    row_groups: metadata.row_groups.length,
    columns: fields.map((f) => ({ ...f })),
  };
  const value = {
    schema_version: "zion-native-table-v1",
    operation,
    quality: "SOURCE_UNVALIDATED",
    normalization: "NATIVE_SOURCE_FIELDS",
    source: {
      store,
      path,
      etag: head.etag,
      bytes: head.size,
      integrity: "ETAG_PINNED_RANGES; FULL_OBJECT_SHA_NOT_REHASHED",
    },
    data: schema,
    cache: "MISS",
  };
  if (operation === "rows") {
    if (
      metadata.schema[0].num_children !== fields.length ||
      fields.some((f) => f.num_children || f.repetition_type === "REPEATED")
    )
      fail("NESTED_SCHEMA_REQUIRES_DEDICATED_ADAPTER", 422);
    const selected = columns ?? fields.map((f) => f.name);
    if (
      selected.length > 256 ||
      selected.some((x) => !fields.some((f) => f.name === x))
    )
      fail("INVALID_COLUMNS");
    if (offset > count) fail("ROW_OFFSET_OUTSIDE_OBJECT", 416);
    const end = Math.min(count, offset + limit);
    let position = 0,
      decoded = 0;
    for (const group of metadata.row_groups) {
      const next = position + Number(group.num_rows);
      if (next > offset && position < end)
        for (const c of group.columns)
          if (selected.includes(c.meta_data?.path_in_schema[0]))
            decoded += Number(c.meta_data.total_uncompressed_size);
      position = next;
    }
    if (!Number.isSafeInteger(decoded) || decoded > MAX_DECODE)
      fail("DECODE_BUDGET_EXCEEDED", 413);
    const decodeMetadata = structuredClone(metadata),
      decimals = new Map();
    for (const field of decodeMetadata.schema) {
      if (
        field.converted_type === "DECIMAL" ||
        field.logical_type?.type === "DECIMAL"
      ) {
        decimals.set(field.name, field.scale ?? field.logical_type?.scale ?? 0);
        delete field.converted_type;
        delete field.logical_type;
      }
    }
    const bounded = (fn) => (input, length) => {
      if (!Number.isSafeInteger(length) || length < 0 || length > MAX_DECODE)
        fail("DECODE_BUDGET_EXCEEDED", 413);
      const output = new Uint8Array(length);
      return fn(input, output) ?? output;
    };
    const compressors = {
      SNAPPY: bounded(snappyUncompress),
      GZIP: decompressGzip,
      ZSTD: decompressZstd,
    };
    let rows;
    try {
      rows = await parquetReadObjects({
        file,
        metadata: decodeMetadata,
        columns: selected,
        rowStart: offset,
        rowEnd: end,
        utf8: false,
        compressors,
        parsers: {
          jsonFromBytes: (bytes) => ({
            encoding: "utf8-json",
            text: new TextDecoder().decode(bytes),
          }),
          dateFromDays: (n) =>
            new Date(n * 86400000).toISOString().slice(0, 10),
          timestampFromMilliseconds: (n) => ({
            unit: "milliseconds",
            epoch: String(n),
          }),
          timestampFromMicroseconds: (n) => ({
            unit: "microseconds",
            epoch: String(n),
          }),
          timestampFromNanoseconds: (n) => ({
            unit: "nanoseconds",
            epoch: String(n),
          }),
        },
      });
    } catch (e) {
      if (e instanceof NativeFailure) throw e;
      fail("PARQUET_ROWS_UNAVAILABLE", 422);
    }
    if (rows.length !== end - offset) fail("PARQUET_ROW_COUNT_MISMATCH", 503);
    value.data = {
      columns: schema.columns.filter((f) => selected.includes(f.name)),
      row_count: count,
      row_offset: offset,
      rows: rows.map((row) =>
        Object.fromEntries(
          selected.map((k) => [
            k,
            decimals.has(k)
              ? decimal(row[k], decimals.get(k))
              : nativeValue(row[k]),
          ]),
        ),
      ),
    };
    value.next_cursor =
      end < count
        ? cursorEncode({
            v: VERSION,
            store,
            path,
            etag: head.etag,
            columns,
            offset: end,
          })
        : null;
  }
  value.source.bytes_read = file.bytesRead;
  const raw = encode(value);
  if (new TextEncoder().encode(raw).length > MAX_RESPONSE)
    fail("RESPONSE_BUDGET_EXCEEDED", 413);
  await env.FINANCIAL_DATA.put(cacheKey, raw, {
    httpMetadata: { contentType: "application/json" },
    customMetadata: { sha256: await digest(raw) },
    onlyIf: { etagDoesNotMatch: "*" },
  });
  return value;
}
export async function handleNative(request, env) {
  try {
    return json(await query(request, env));
  } catch (e) {
    return json(
      {
        error: {
          code:
            e instanceof NativeFailure ? e.code : "NATIVE_QUERY_UNAVAILABLE",
        },
      },
      e instanceof NativeFailure ? e.status : 503,
    );
  }
}
