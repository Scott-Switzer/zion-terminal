import { digest, encode, NativeFailure } from "./native.mjs";
const PREFIX = "gold/collection-catalog/releases/";
const cache = new Map();
async function verified(env, reference) {
  if (
    !reference?.key?.startsWith(PREFIX) ||
    reference.key.split("/").some((x) => x === "." || x === "..") ||
    !/^[a-f0-9]{64}$/.test(reference.sha256)
  )
    throw new NativeFailure("CATALOG_REFERENCE_INVALID", 503);
  const id = reference.key + reference.sha256;
  if (cache.has(id)) return cache.get(id);
  const object = await env.FINANCIAL_DATA.get(reference.key);
  if (!object || object.size > 1048576)
    throw new NativeFailure("CATALOG_UNAVAILABLE", 503);
  const raw = await object.text();
  if ((await digest(raw)) !== reference.sha256)
    throw new NativeFailure("CATALOG_INTEGRITY_FAILURE", 503);
  const value = JSON.parse(raw);
  if (cache.size >= 8) cache.delete(cache.keys().next().value);
  cache.set(id, value);
  return value;
}
export async function handleCatalog(request, env) {
  const headers = {
    "content-type": "application/json",
    "cache-control": "private, no-store",
    "x-content-type-options": "nosniff",
  };
  try {
    if (request.method !== "GET") throw new NativeFailure("READ_ONLY", 405);
    const url = new URL(request.url),
      match = /^\/v1\/datasets\/catalog(?:\/([a-f0-9]{32}))?$/.exec(
        url.pathname,
      );
    if (!match) throw new NativeFailure("NOT_FOUND", 404);
    const allowed = match[1] ? [] : ["search", "family", "offset", "limit"];
    for (const k of url.searchParams.keys())
      if (!allowed.includes(k) || url.searchParams.getAll(k).length !== 1)
        throw new NativeFailure("INVALID_PARAMETER");
    const root = await verified(env, {
      key: env.COLLECTION_CATALOG_KEY,
      sha256: env.COLLECTION_CATALOG_SHA256,
    });
    if (match[1]) {
      const entry = root.entries.find((e) => e.id === match[1]);
      if (!entry) throw new NativeFailure("DATASET_NOT_FOUND", 404);
      return new Response(
        encode({
          ...(await verified(env, entry.reference)),
          catalog_release_id: root.release_id,
        }),
        { headers },
      );
    }
    const q = url.searchParams,
      search = q.get("search") ?? "",
      family = q.get("family");
    if (search.length > 128 || family?.length > 128)
      throw new NativeFailure("INVALID_PARAMETER");
    for (const k of ["offset", "limit"])
      if (q.has(k) && !/^\d{1,10}$/.test(q.get(k)))
        throw new NativeFailure("INVALID_INTEGER");
    const offset = Number(q.get("offset") ?? 0),
      limit = Number(q.get("limit") ?? 50);
    if (limit < 1 || limit > 200) throw new NativeFailure("INVALID_LIMIT");
    const entries = root.entries.filter(
      (e) =>
        (!family || e.family === family) &&
        (!search ||
          (e.dataset ?? "").toLowerCase().includes(search.toLowerCase())),
    );
    const { entries: all, ...summary } = root;
    return new Response(
      encode({
        ...summary,
        matching_releases: entries.length,
        offset,
        entries: entries.slice(offset, offset + limit),
        next_offset: offset + limit < entries.length ? offset + limit : null,
      }),
      { headers },
    );
  } catch (e) {
    return new Response(
      encode({
        error: {
          code: e instanceof NativeFailure ? e.code : "CATALOG_UNAVAILABLE",
        },
      }),
      { status: e instanceof NativeFailure ? e.status : 503, headers },
    );
  }
}
