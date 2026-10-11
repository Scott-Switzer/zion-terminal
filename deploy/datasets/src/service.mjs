// Storage access is not financial normalization or permission to redistribute.
export const STORES = Object.freeze({
  financial: { binding: 'FINANCIAL_DATA', label: 'Financial market and SEC datasets', usage: 'Source data, normalized datasets, and published releases' },
  research: { binding: 'RESEARCH_DATA', label: 'Private research and WRDS datasets', usage: 'Private research only; provider restrictions apply' },
  filings: { binding: 'SEC_CORPUS', label: 'SEC filing intelligence', usage: 'Filing documents and extraction evidence' },
  lakehouse: { binding: 'RESEARCH_LAKEHOUSE', label: 'Research lakehouse', usage: 'Iceberg data and metadata; use a compatible query engine' },
});
export const MAX_BYTES = 1024 * 1024;
const HEADERS = { 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff' };
export class Failure extends Error {
  constructor(message, status = 400) { super(message); this.status = status; }
}
function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { ...HEADERS, 'Content-Type': 'application/json' } });
}
function path(value, empty = false) {
  if (typeof value !== 'string' || (!empty && !value) || value.length > 1024 ||
      /[\\\x00-\x1f\x7f]/.test(value) || value.startsWith('/') ||
      value.split('/').some(x => x === '.' || x === '..')) throw new Failure('Invalid relative path.');
  return value;
}
function integer(value, fallback, min, max) {
  if (value === null) return fallback;
  if (!/^\d{1,16}$/.test(value)) throw new Failure('Invalid integer.');
  const n = Number(value);
  if (!Number.isSafeInteger(n) || n < min || n > max) throw new Failure('Integer outside supported range.');
  return n;
}
function parameters(url, allowed) {
  for (const key of url.searchParams.keys()) {
    if (!allowed.includes(key) || url.searchParams.getAll(key).length !== 1) throw new Failure('Unsupported or repeated parameter.');
  }
  return url.searchParams;
}
function metadata(object) {
  return { path: object.key, bytes: object.size, etag: object.etag,
    stored_at: object.uploaded?.toISOString() ?? null,
    availability_date: null, quality: 'SOURCE_UNVALIDATED' };
}
function encodeCursor(store, prefix, recursive, cursor) {
  return btoa(encodeURIComponent(JSON.stringify({ store, prefix, recursive, cursor })));
}
function decodeCursor(token, store, prefix, recursive) {
  if (!token || token.length > 8192) throw new Failure('Invalid cursor.');
  try {
    const d = JSON.parse(decodeURIComponent(atob(token)));
    if (d.store !== store || d.prefix !== prefix || d.recursive !== recursive || typeof d.cursor !== 'string') throw new Error();
    return d.cursor;
  } catch { throw new Failure('Cursor belongs to another query or is invalid.'); }
}
export async function catalog(env, probe = false) {
  const sources = await Promise.all(Object.entries(STORES).map(async ([id, s]) => {
    const result = { id, name: s.label, usage: s.usage, access: 'PRIVATE_SERVICE_ONLY',
      valuation_autofill: id === 'financial' ? 'APPROVED_SEC_PACKETS_ONLY' : 'NOT_MAPPED',
      connectivity: 'CONFIGURED', quality: 'SOURCE_UNVALIDATED' };
    if (!env[s.binding]) return { ...result, connectivity: 'NOT_CONFIGURED' };
    if (probe) {
      try {
        const page = await env[s.binding].list({ limit: 1 });
        result.connectivity = page.objects.length ? 'READABLE' : 'EMPTY';
      } catch { result.connectivity = 'UNAVAILABLE'; }
    }
    return result;
  }));
  return { schema_version: 'zion-dataset-catalog-v1', checked_at: probe ? new Date().toISOString() : null,
    scope: 'Financial R2 stores; unrelated backups and nonfinancial stores excluded',
    limits: { objects_per_page: 200, bytes_per_read: MAX_BYTES },
    warning: 'Stored timestamps are not observation or availability dates. Connectivity does not establish freshness, financial accuracy, or redistribution rights.', sources };
}
export async function handle(request, env, publicCatalog = false) {
  try {
    const u = new URL(request.url);
    if (request.method !== 'GET' && request.method !== 'HEAD') throw new Failure('Read-only service.', 405);
    if (publicCatalog) {
      if (request.method !== 'GET' || u.pathname !== '/v1/datasets/status' || u.search) throw new Failure('Not found.', 404);
      return json(await catalog(env, true));
    }
    if (request.method === 'GET' && ['/v1/datasets', '/healthz'].includes(u.pathname)) {
      parameters(u, []);
      return json(await catalog(env, u.pathname === '/healthz'));
    }
    const match = /^\/v1\/datasets\/([a-z]+)\/(objects|object)$/.exec(u.pathname);
    if (!match || !STORES[match[1]]) throw new Failure('Not found.', 404);
    const [ , id, action ] = match;
    const bucket = env[STORES[id].binding];
    if (!bucket) throw new Failure('Dataset store unavailable.', 503);
    if (action === 'objects') {
      if (request.method !== 'GET') throw new Failure('Method not allowed.', 405);
      const q = parameters(u, ['prefix', 'cursor', 'limit', 'recursive']);
      const prefix = path(q.get('prefix') ?? '', true);
      const recursive = q.get('recursive') ?? 'false';
      if (!['true', 'false'].includes(recursive)) throw new Failure('Invalid recursive flag.');
      const options = { prefix, limit: integer(q.get('limit'), 50, 1, 200) };
      if (recursive === 'false') options.delimiter = '/';
      if (q.has('cursor')) options.cursor = decodeCursor(q.get('cursor'), id, prefix, recursive);
      const page = await bucket.list(options);
      // Defense in depth: never return rows outside the requested namespace.
      if (page.objects.some(x => !x.key.startsWith(prefix)) || (page.delimitedPrefixes ?? []).some(x => !x.startsWith(prefix))) throw new Failure('Invalid storage listing.', 503);
      return json({ store: id, prefix, objects: page.objects.map(metadata), prefixes: page.delimitedPrefixes ?? [],
        truncated: page.truncated, cursor: page.truncated ? encodeCursor(id, prefix, recursive, page.cursor) : null });
    }
    const q = parameters(u, ['path', 'offset', 'length']);
    const key = path(q.get('path'));
    const offset = integer(q.get('offset'), 0, 0, Number.MAX_SAFE_INTEGER);
    const length = integer(q.get('length'), null, 1, MAX_BYTES);
    const head = await bucket.head(key);
    if (!head) throw new Failure('Object not found.', 404);
    if (request.method === 'HEAD') return new Response(null, { headers: { ...HEADERS,
      ETag: head.httpEtag, 'Content-Length': String(head.size), 'Accept-Ranges': 'bytes' } });
    const etag = request.headers.get('If-Match');
    if (!etag || etag === '*' || etag !== head.httpEtag) throw new Failure('Exact If-Match from HEAD or listing is required.', etag ? 412 : 428);
    if (offset >= head.size && head.size > 0) throw new Failure('Range outside object.', 416);
    if (length === null && head.size > MAX_BYTES) throw new Failure('Use an explicit bounded offset and length for large objects.', 413);
    if (length === null && offset !== 0) throw new Failure('Length required with offset.');
    const size = Math.min(length ?? head.size, head.size - offset);
    if (size < 0 || (head.size === 0 && offset !== 0)) throw new Failure('Range outside object.', 416);
    const opts = { onlyIf: { etagMatches: head.etag } };
    if (size > 0) opts.range = { offset, length: size };
    const object = await bucket.get(key, opts);
    if (!object) throw new Failure('Object no longer exists.', 404);
    if (!object.body || object.etag !== head.etag) throw new Failure('Object changed during read.', 412);
    const ranged = length !== null && head.size > 0;
    const headers = { ...HEADERS, 'Content-Type': 'application/octet-stream', ETag: object.httpEtag,
      'Content-Length': String(size), 'Accept-Ranges': 'bytes', 'X-Data-Quality': 'SOURCE_UNVALIDATED' };
    if (ranged) headers['Content-Range'] = `bytes ${offset}-${offset + size - 1}/${head.size}`;
    // Return the R2 stream directly. No text/JSON/Parquet decode or whole-file buffering.
    return new Response(object.body, { status: ranged ? 206 : 200, headers });
  } catch (error) {
    return json({ error: error instanceof Failure ? error.message : 'Dataset storage temporarily unavailable.' }, error instanceof Failure ? error.status : 503);
  }
}
