import test from 'node:test';
import assert from 'node:assert/strict';
import { handle, STORES, MAX_BYTES } from '../../deploy/datasets/src/service.mjs';

class Bucket {
  constructor() { this.calls = []; this.raw = new TextEncoder().encode('financial facts'); this.etag = 'v1'; this.size = this.raw.length; }
  metadata(key) { return { key, size: this.size, etag: this.etag, httpEtag: `"${this.etag}"`, uploaded: new Date('2026-10-01') }; }
  async list(opts) {
    this.calls.push(['list', opts]);
    return this.page ?? { objects: [this.metadata(`${opts.prefix ?? ''}data.json`)], delimitedPrefixes: [], truncated: false };
  }
  async head(key) { this.calls.push(['head', key]); return this.missing ? null : this.metadata(key); }
  async get(key, opts) {
    this.calls.push(['get', key, opts]);
    if (this.changed) return this.metadata(key);
    let raw = this.raw;
    if (opts.range) raw = raw.slice(opts.range.offset, opts.range.offset + opts.range.length);
    return { ...this.metadata(key), body: new Response(raw).body };
  }
}
function fixture() { const bucket = new Bucket(); return { bucket, env: Object.fromEntries(Object.values(STORES).map(x => [x.binding, bucket])) }; }
function req(path, options) { return new Request(`https://zion.internal${path}`, options); }

test('catalog lists every financial store without asserting freshness', async () => {
  const { env, bucket } = fixture(); const r = await handle(req('/v1/datasets'), env);
  const d = await r.json(); assert.equal(d.sources.length, 4); assert.equal(bucket.calls.length, 0);
  assert(d.sources.every(x => x.quality === 'SOURCE_UNVALIDATED'));
});
test('restricted catalog probes actual storage but never returns paths', async () => {
  const { env } = fixture(); const r = await handle(req('/v1/datasets/status'), env, true);
  const d = await r.json(); assert(d.sources.every(x => x.connectivity === 'READABLE'));
  assert(!JSON.stringify(d).includes('data.json'));
  for (const url of ['/v1/datasets/research/object?path=secret', '/v1/datasets/research/objects', '/healthz', '/v1/datasets/status?path=secret'])
    assert.equal((await handle(req(url), env, true)).status, 404);
});
test('one unavailable bucket does not fail the other catalog sources', async () => {
  const { env } = fixture(); env.RESEARCH_DATA = { list() { throw new Error('SECRET'); } };
  const d = await (await handle(req('/v1/datasets/status'), env, true)).json();
  assert.equal(d.sources[1].connectivity, 'UNAVAILABLE'); assert.equal(d.sources[0].connectivity, 'READABLE');
  assert(!JSON.stringify(d).includes('SECRET'));
});
test('pagination cursor preserves scope and honors returned truncated flag', async () => {
  const { env, bucket } = fixture(); bucket.page = { objects: [], delimitedPrefixes: ['raw/wrds/next/'], truncated: true, cursor: 'r2opaque' };
  const base = '/v1/datasets/research/objects?prefix=raw/wrds/';
  const d = await (await handle(req(base), env)).json(); assert(d.cursor); assert.equal(d.truncated, true);
  assert.equal((await handle(req(base + '&cursor=' + encodeURIComponent(d.cursor)), env)).status, 200);
  assert.equal(bucket.calls.at(-1)[1].cursor, 'r2opaque');
  assert.equal((await handle(req('/v1/datasets/financial/objects?prefix=raw/wrds/&cursor=' + encodeURIComponent(d.cursor)), env)).status, 400);
});
test('rejects writes, unknown stores, duplicate arguments and traversal without reading', async () => {
  const { env, bucket } = fixture();
  for (const [url, options, status] of [
    ['/v1/datasets', { method: 'POST' }, 405],
    ['/v1/datasets/secrets/objects', {}, 404],
    ['/v1/datasets/research/objects?limit=1000', {}, 400],
    ['/v1/datasets/research/objects?prefix=../', {}, 400],
    ['/v1/datasets/research/object?path=a%00b', {}, 400],
    ['/v1/datasets/research/object?path=x&path=y', {}, 400],
    ['/v1/datasets/research/objects?recursive=yes', {}, 400],
    ['/v1/datasets/research/objects?cursor=bad', {}, 400],
  ]) assert.equal((await handle(req(url, options), env)).status, status);
  assert.equal(bucket.calls.length, 0);
});
test('head metadata enables conditional bounded streaming read', async () => {
  const { env, bucket } = fixture(); const url = '/v1/datasets/research/object?path=raw/wrds/table/data.parquet';
  const h = await handle(req(url, { method: 'HEAD' }), env); assert.equal(h.headers.get('etag'), '"v1"');
  assert.equal(bucket.calls.length, 1);
  const r = await handle(req(url, { headers: { 'If-Match': h.headers.get('etag') } }), env);
  assert.equal(r.status, 200); assert.equal(await r.text(), 'financial facts');
  assert.deepEqual(bucket.calls.at(-1)[2].onlyIf, { etagMatches: 'v1' });
  assert.equal(r.headers.get('x-data-quality'), 'SOURCE_UNVALIDATED');
});
test('range reads allow large archives without buffering or whole-file access', async () => {
  const { env, bucket } = fixture(); bucket.size = MAX_BYTES * 500;
  const url = '/v1/datasets/research/object?path=sec/companyfacts.zip'; const opts = { headers: { 'If-Match': '"v1"' } };
  assert.equal((await handle(req(url, opts), env)).status, 413);
  assert.equal(bucket.calls.filter(x => x[0] === 'get').length, 0);
  const r = await handle(req(url + '&offset=1&length=4', opts), env);
  assert.equal(r.status, 206); assert.equal(await r.text(), 'inan');
  assert.equal(r.headers.get('content-range'), `bytes 1-4/${bucket.size}`);
  assert.equal((await handle(req(url + '&length=' + (MAX_BYTES + 1), opts), env)).status, 400);
});
test('etag changes and invalid ranges fail closed', async () => {
  const { env, bucket } = fixture(); const url = '/v1/datasets/research/object?path=data';
  assert.equal((await handle(req(url), env)).status, 428);
  assert.equal((await handle(req(url, { headers: { 'If-Match': '*' } }), env)).status, 412);
  assert.equal((await handle(req(url, { headers: { 'If-Match': '"old"' } }), env)).status, 412);
  assert.equal((await handle(req(url + '&offset=100&length=2', { headers: { 'If-Match': '"v1"' } }), env)).status, 416);
  assert.equal(bucket.calls.filter(x => x[0] === 'get').length, 0);
  bucket.changed = true;
  assert.equal((await handle(req(url, { headers: { 'If-Match': '"v1"' } }), env)).status, 412);
});
test('missing/empty objects and namespace leakage are handled explicitly', async () => {
  const { env, bucket } = fixture(); const url = '/v1/datasets/financial/object?path=data';
  bucket.missing = true; assert.equal((await handle(req(url), env)).status, 404);
  bucket.missing = false; bucket.size = 0; bucket.raw = new Uint8Array();
  const r = await handle(req(url, { headers: { 'If-Match': '"v1"' } }), env);
  assert.equal(r.status, 200); assert.equal(await r.text(), '');
  bucket.page = { objects: [{ key: 'outside/data' }], truncated: false };
  assert.equal((await handle(req('/v1/datasets/financial/objects?prefix=safe/'), env)).status, 503);
});
test('unexpected errors do not expose secrets', async () => {
  const { env } = fixture(); env.RESEARCH_DATA.head = () => { throw new Error('TOKEN=private'); };
  const r = await handle(req('/v1/datasets/research/object?path=data'), env);
  assert.equal(r.status, 503); assert(!((await r.text()).includes('TOKEN')));
});
test('unicode prefixes survive cursor roundtrips', async () => {
  const { env, bucket } = fixture(); bucket.page = { objects: [], delimitedPrefixes: [], truncated: true, cursor: 'next' };
  const base = '/v1/datasets/research/objects?prefix=' + encodeURIComponent('étude/');
  const d = await (await handle(req(base), env)).json(); assert(d.cursor);
  assert.equal((await handle(req(base + '&cursor=' + encodeURIComponent(d.cursor)), env)).status, 200);
});
test('local bridge rejects non-loopback and browser requests before forwarding', async () => {
  const bridge = (await import('../../deploy/datasets/client/worker.mjs')).default;
  let calls = 0; const env = { DATASETS: { fetch: () => { calls++; return new Response('ok'); } } };
  assert.equal((await bridge.fetch(new Request('http://evil.example/v1/datasets'), env)).status, 403);
  assert.equal((await bridge.fetch(new Request('http://127.0.0.1/v1/datasets', { headers: { Origin: 'http://evil.example' } }), env)).status, 403);
  assert.equal(calls, 0);
  assert.equal((await bridge.fetch(new Request('http://127.0.0.1/v1/datasets'), env)).status, 200);
  assert.equal(calls, 1);
});
