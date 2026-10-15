"""The engine's derived cache is namespaced by the credential that authenticates it.

Rotating the private bearer used to turn every cached query into a permanent
`CACHE_INTEGRITY_FAILURE`, because the envelope of one credential cannot verify under
another and the writer is create-only. These tests pin the namespace, the healed rotation
and the integrity check that must survive both.
"""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from private_financials import PrivateFinancialsError
from sec_engine import encoded
from sec_engine_api import cache_scope, persist, scoped_key, stored

KEY = "gold/sec-engine/releases/" + "b" * 64 + "/queries/" + "c" * 64 + ".json"


class Bucket:
    def __init__(self):
        self.objects = {}

    async def get(self, key):
        if key not in self.objects:
            return None
        raw = self.objects[key]

        async def text():
            return raw.decode()

        return SimpleNamespace(size=len(raw), text=text)

    async def put(self, key, body, *options):
        self.objects[key] = body.encode() if isinstance(body, str) else body
        return SimpleNamespace()


def env(secret):
    return SimpleNamespace(MARKET_DATA=Bucket(), ZION_PRIVATE_API_TOKEN=secret)


def test_scope_is_stable_per_credential_and_reveals_nothing():
    first = cache_scope("credential-one")
    assert first == cache_scope("credential-one")
    assert first != cache_scope("credential-two")
    assert "credential-one" not in first
    assert len(first) == 64
    with pytest.raises(PrivateFinancialsError) as raised:
        cache_scope("")
    assert raised.value.code == "PRIVATE_API_UNCONFIGURED"


def test_scoped_key_namespaces_without_losing_the_object_path():
    scoped = scoped_key(KEY, "credential-one")
    assert scoped == "gold/cache-scopes/" + cache_scope("credential-one") + "/" + KEY[len("gold/"):]
    assert scoped.endswith("b" * 64 + "/queries/" + "c" * 64 + ".json")
    assert scoped_key(KEY, "credential-two") != scoped


def test_round_trip_under_one_credential():
    one = env("credential-one")
    asyncio.run(persist(one, KEY, {"observations": [1]}, "credential-one"))
    assert asyncio.run(stored(one, KEY, "credential-one")) == {"observations": [1]}
    assert KEY not in one.MARKET_DATA.objects, "the unscoped key must stay untouched"


def test_rotation_reads_its_own_namespace_and_heals_the_cache():
    one, two = env("credential-one"), env("credential-two")
    asyncio.run(persist(one, KEY, {"observations": ["old"]}, "credential-one"))
    # The rotated credential sees no object at all rather than another credential's envelope.
    assert asyncio.run(stored(two, KEY, "credential-two")) is None
    # ... and can therefore write its own, which the old object could never have blocked.
    asyncio.run(persist(two, KEY, {"observations": ["new"]}, "credential-two"))
    assert asyncio.run(stored(two, KEY, "credential-two")) == {"observations": ["new"]}
    assert asyncio.run(stored(one, KEY, "credential-one")) == {"observations": ["old"]}


def test_tampering_under_the_same_scope_is_still_refused():
    one = env("credential-one")
    asyncio.run(persist(one, KEY, {"observations": [1]}, "credential-one"))
    scoped = scoped_key(KEY, "credential-one")
    envelope = json.loads(one.MARKET_DATA.objects[scoped])
    envelope["value"]["observations"] = [999]
    one.MARKET_DATA.objects[scoped] = encoded(envelope)
    with pytest.raises(PrivateFinancialsError) as raised:
        asyncio.run(stored(one, KEY, "credential-one"))
    assert raised.value.code == "CACHE_INTEGRITY_FAILURE"


def test_an_envelope_signed_by_another_credential_is_never_trusted():
    one, two = env("credential-one"), env("credential-two")
    asyncio.run(persist(one, KEY, {"observations": [1]}, "credential-one"))
    # Even if the old envelope is placed where the rotated credential would look, the MAC
    # check still fails: the namespace removes the collision, it does not skip verification.
    moved = scoped_key(KEY, "credential-two")
    one.MARKET_DATA.objects[moved] = one.MARKET_DATA.objects[scoped_key(KEY, "credential-one")]
    with pytest.raises(PrivateFinancialsError) as raised:
        asyncio.run(stored(one, KEY, "credential-two"))
    assert raised.value.code == "CACHE_INTEGRITY_FAILURE"
    assert hashlib.sha256(b"unrelated").hexdigest()  # the digest helper stays importable
