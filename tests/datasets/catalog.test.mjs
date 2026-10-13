import test from "node:test";
import assert from "node:assert/strict";
import { handleCatalog } from "../../deploy/datasets/src/catalog.mjs";
import { digest } from "../../deploy/datasets/src/native.mjs";
const prefix = "gold/collection-catalog/releases/" + "a".repeat(64) + "/";
async function fixture() {
  const detail = JSON.stringify({
    id: "b".repeat(32),
    columns: [{ name: "native_thing", dtype: "INT64" }],
    objects: [],
  });
  const key = prefix + "detail.json";
  const root = JSON.stringify({
    release_id: "a".repeat(64),
    entries: [
      {
        id: "b".repeat(32),
        dataset: "crsp__msf",
        family: "crsp",
        reference: { key, sha256: await digest(detail) },
      },
      { id: "c".repeat(32), dataset: null, family: "ff" },
    ],
  });
  const values = new Map([
    [prefix + "manifest.json", root],
    [key, detail],
  ]);
  return {
    values,
    env: {
      COLLECTION_CATALOG_KEY: prefix + "manifest.json",
      COLLECTION_CATALOG_SHA256: await digest(root),
      FINANCIAL_DATA: {
        get: async (key) =>
          values.has(key)
            ? {
                size: values.get(key).length,
                text: async () => values.get(key),
              }
            : null,
      },
    },
  };
}
const req = (path) =>
  new Request("https://private.internal/v1/datasets/catalog" + path);
test("catalog searches and pages source-declared records with unknown dataset identity retained", async () => {
  const { env } = await fixture();
  const r = await (
    await handleCatalog(req("?search=CRSP&limit=1"), env)
  ).json();
  assert.equal(r.entries[0].dataset, "crsp__msf");
  assert.equal(r.matching_releases, 1);
  const all = await (await handleCatalog(req("?limit=1"), env)).json();
  assert.equal(all.next_offset, 1);
  const detail = await (
    await handleCatalog(req("/" + "b".repeat(32)), env)
  ).json();
  assert.equal(detail.columns[0].name, "native_thing");
});
test("catalog fails closed for corrupt pins and invalid query parameters", async () => {
  const { env } = await fixture();
  env.COLLECTION_CATALOG_SHA256 = "e".repeat(64);
  assert.equal((await handleCatalog(req(""), env)).status, 503);
  assert.equal((await handleCatalog(req("?sql=drop"), env)).status, 400);
});
