"""The local dev harness must stay coherent with the deployment configuration.

`thin/wrangler.dev.jsonc` is what a local DCF Worker actually talks to, so a drift here
would be invisible until a live run silently read the wrong release or, worse, wrote a
derived cache envelope into a bucket deployments read.
"""

import json
import re
from pathlib import Path

DEV = Path(__file__).resolve().parents[2] / "deploy/cloudflare-query/thin/wrangler.dev.jsonc"
LIVE = DEV.parent / "wrangler.jsonc"

PINNED_VARS = (
    "SEC_ENGINE_MANIFEST_KEY",
    "SEC_ENGINE_MANIFEST_SHA256",
    "SEC_COMPANYFACTS_MANIFEST_KEY",
    "SEC_COMPANYFACTS_MANIFEST_SHA256",
    "SEC_HISTORY_MANIFEST_KEY",
    "SEC_HISTORY_MANIFEST_SHA256",
    "SEC_EXPANDED_INDEX_KEY",
    "SEC_EXPANDED_INDEX_SHA256",
)


def stripped(path):
    text = path.read_text()
    text = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
    return json.loads(text)


def test_dev_harness_pins_the_same_releases_as_the_deployment():
    dev, live = stripped(DEV), stripped(LIVE)
    for name in PINNED_VARS:
        assert dev["vars"].get(name) == live["vars"].get(name), name


def test_dev_harness_serves_the_name_the_service_binding_targets():
    assert stripped(DEV)["name"] == stripped(LIVE)["name"]


def test_derived_cache_bucket_is_local_and_source_archives_stay_remote():
    buckets = {item["binding"]: item for item in stripped(DEV)["r2_buckets"]}
    # The engine's derived query cache is authenticated with the deployment credential, so
    # a local run must neither read nor write it.
    assert not buckets["MARKET_DATA"].get("remote"), "MARKET_DATA must stay local in dev"
    for binding in ("SEC_CORPUS", "SEC_ARCHIVE"):
        assert buckets[binding].get("remote") is True, binding


def test_engine_flag_enables_the_routes_the_consumer_calls():
    dev = stripped(DEV)
    assert dev["vars"]["SEC_ENGINE_ENABLED"] == "true"
