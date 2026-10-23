import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / 'deploy/cloudflare-query/src'))


def module():
    return importlib.import_module('snapshot_bundle')


def test_bundle_is_order_independent_and_every_source_changes_identity():
    b = module()
    sources = {'SEC': 'sec-original', 'CRSP': 'crsp-original', 'serving': 'serving-original'}
    original = b.bundle_id(sources)
    assert original == b.bundle_id(dict(reversed(list(sources.items()))))
    for name in sources:
        assert original != b.bundle_id({**sources, name: sources[name] + '-next'})
    assert original != b.bundle_id({**sources, 'additional': 'release'})


def test_coverage_uses_observation_dates_never_publication_or_forecast_dates():
    b = module()
    value = {'observations': [{'period_end': '2025-09-30', 'available_at': '2026-02-01T00:00:00Z'}],
             'forecast': {'period_end': '2030-12-31'}, 'source_cutoff': '2026-10-22'}
    assert b.data_as_of(value) == '2025-09-30'
    assert b.data_as_of({'observations': []}) is None
    assert b.data_as_of({'prices': [{'session_date': '2025-12-31'}]}) == '2025-12-31'


def test_stamp_preserves_source_provenance_and_checks_pin():
    b = module()
    snapshot = {'bundle_id': b.bundle_id({'SEC': 'original'}), 'source_releases': {'SEC': 'original'}}
    source = {'provider': 'SEC', 'release': 'original'}
    result = b.stamp({'source': source, 'observations': [{'period_end': '2025-12-31'}]}, snapshot)
    assert result['bundle_id'] == snapshot['bundle_id']
    assert result['source'] == source
    assert result['data_as_of'] == '2025-12-31'
    with pytest.raises(ValueError, match='RELEASE_MISMATCH'):
        b.assert_bundle('different', snapshot)


def test_manifest_tracks_configured_and_shared_source_releases(monkeypatch):
    b = module()
    import serving_v2
    async def serving(env, **kwargs):
        return {}, {'serving_release_id': 'serving-original', 'source': {'fundamentals': {'snapshot_id': 'fund-original'}}}
    monkeypatch.setattr(serving_v2, 'load_release', serving)
    full = b.BASE + 'full-history-2026-10-21-v1/plan.json'
    job = b.BASE + 'source-enrichment-2026-10-21-v1/job.json'
    daily = 'control/daily-prices/CURRENT.json'
    digest = 'a' * 64
    objects = {full: {'input_sha256': 'crsp-original'}, job: {'run_id': 'sec-packets-original'},
               daily: {'sha256': digest, 'data_key': f'gold/daily-prices/releases/{digest}/data.json'}}
    class Bucket:
        async def get(self, key):
            value = objects.get(key)
            if value is None:
                return None
            raw = json.dumps(value)
            async def text():
                return raw
            return SimpleNamespace(size=len(raw), text=text)
    env = SimpleNamespace(MARKET_DATA=Bucket(), BUNDLE_SHARED_SNAPSHOT='true', SEC_ENGINE_MANIFEST_KEY='gold/sec-engine/releases/' + 'c'*64 + '/manifest.json')
    original = asyncio.run(b.load_snapshot(env))
    assert original['source_releases']['SEC_ENGINE_MANIFEST_KEY'] == 'c'*64
    for key, field in ((full, 'input_sha256'), (job, 'run_id')):
        before = objects[key][field]
        objects[key][field] += '-next'
        assert asyncio.run(b.load_snapshot(env))['bundle_id'] != original['bundle_id']
        objects[key][field] = before
    objects[daily] = {'sha256': 'd'*64, 'data_key': 'gold/daily-prices/releases/' + 'd'*64 + '/data.json'}
    assert asyncio.run(b.load_snapshot(env))['bundle_id'] != original['bundle_id']
    del objects[job]
    with pytest.raises(ValueError, match='BUNDLE_REFERENCE_UNAVAILABLE'):
        asyncio.run(b.load_snapshot(env))


def test_thin_http_stamps_answers_and_rejects_a_mid_request_change(monkeypatch):
    import importlib.util
    class Response:
        def __init__(self, body, status=200, headers=None):
            self.value = json.loads(body)
            self.status = status
            self.headers = headers or {}
    monkeypatch.setitem(sys.modules, 'workers', SimpleNamespace(Response=Response, WorkerEntrypoint=object))
    spec = importlib.util.spec_from_file_location('bundle_test_thin', Path(__file__).parents[2] / 'deploy/cloudflare-query/src/thin_worker.py')
    thin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(thin)
    values = [{'bundle_id':'a'*64, 'source_releases':{'SEC':'original'}}]*2
    async def snapshot(env):
        return values.pop(0)
    async def answer(self, request):
        return self._response({'source':{'provider':'SEC','release':'original'}, 'observations':[{'period':'2025-12-31'}]})
    monkeypatch.setattr(thin, 'load_snapshot', snapshot)
    monkeypatch.setattr(thin.Default, '_fetch', answer)
    worker = thin.Default(); worker.env = object()
    request = SimpleNamespace(url='https://test/v2/tools/get_fundamentals', method='POST', headers={'x-zion-bundle-id':'a'*64})
    result = asyncio.run(worker.fetch(request))
    assert result.status == 200
    assert result.value['bundle_id'] == 'a'*64
    assert result.value['data_as_of'] == '2025-12-31'
    assert result.headers['x-zion-bundle-id'] == 'a'*64
    assert result.headers['x-zion-data-as-of'] == '2025-12-31'
    values[:] = [{'bundle_id':'a'*64,'source_releases':{'SEC':'original'}}, {'bundle_id':'b'*64,'source_releases':{'SEC':'next'}}]
    result = asyncio.run(worker.fetch(request))
    assert result.status == 409 and result.value['error']['code'] == 'RELEASE_MISMATCH'


def test_old_serving_payload_cannot_be_relabelled_with_current_bundle():
    b = module()
    snapshot = {'bundle_id': 'a'*64, 'source_releases': {'serving': 'current'}}
    with pytest.raises(ValueError, match='RELEASE_MISMATCH'):
        b.stamp({'release': {'serving_release_id': 'old'}, 'observations': []}, snapshot)


def test_provider_release_and_digest_are_part_of_snapshot(monkeypatch):
    b = module()
    import serving_v2
    async def serving(env, **kwargs):
        return {}, {'serving_release_id': 'same-serving'}
    monkeypatch.setattr(serving_v2, 'load_release', serving)
    class Bucket:
        async def get(self, key):
            return None
    env = SimpleNamespace(MARKET_DATA=Bucket(),
        PROVIDER_HISTORY_KEY='gold/provider-history/releases/' + 'a'*64 + '/manifest.json',
        PROVIDER_HISTORY_SHA256='b'*64)
    original = asyncio.run(b.load_snapshot(env))
    env.PROVIDER_HISTORY_SHA256 = 'c'*64
    assert asyncio.run(b.load_snapshot(env))['bundle_id'] != original['bundle_id']
    env.PROVIDER_HISTORY_SHA256 = 'b'*64
    env.PROVIDER_HISTORY_KEY = env.PROVIDER_HISTORY_KEY.replace('a'*64, 'd'*64)
    assert asyncio.run(b.load_snapshot(env))['bundle_id'] != original['bundle_id']


def test_historical_reader_shape_preserves_observation_coverage():
    assert module().data_as_of({'series': [{'observations': [
        {'observation_date': '2026-09-30', 'value': '1.2'}]}]}) == '2026-09-30'


def test_index_pin_changes_snapshot_even_when_current_prices_do_not(monkeypatch):
    b = module()
    import serving_v2
    async def serving(env, **kwargs):
        return {}, {'serving_release_id': 'same'}
    monkeypatch.setattr(serving_v2, 'load_release', serving)
    pointer = {'sha256': 'a'*64, 'data_key': 'gold/daily-prices/releases/'+'a'*64+'/data.json'}
    class Bucket:
        async def get(self, key):
            if key != 'control/daily-prices/CURRENT.json': return None
            raw = json.dumps(pointer)
            async def text(): return raw
            return SimpleNamespace(size=len(raw), text=text)
    env = SimpleNamespace(MARKET_DATA=Bucket())
    original = asyncio.run(b.load_snapshot(env))
    pointer['sessions_index'] = {'sha256':'b'*64,'key':'control/daily-prices/sessions/'+'b'*64+'.json','bytes':123}
    updated = asyncio.run(b.load_snapshot(env))
    assert updated['bundle_id'] != original['bundle_id']
    assert updated['source_releases']['daily_session_index'] == 'b'*64


@pytest.mark.parametrize('cik,registry,allowed', [
    ('0000320193','b'*64,True), ('0000320193','c'*64,False),
    ('0000104169','b'*64,False)])
def test_registry_override_preserves_only_declared_unaffected_company_context(monkeypatch,cik,registry,allowed):
    import sec_engine_api as api
    old='d'*64;new='e'*64
    m={'release_id':new,'registry_sha256':'a'*64,'source_releases':[old],
       'registry_change':{'change_class':'REGISTRY_ISSUER_REVENUE_OVERRIDE',
          'affected_ciks':['0000104169'],'registry_sha256_from':'b'*64,'registry_sha256_to':'a'*64}}
    async def artifact(env,ref):
        return {'release_id':old,'registry_sha256':registry,'cik':cik,'rights':'PUBLIC_SEC_ONLY','observations':[]}
    monkeypatch.setattr(api,'artifact',artifact)
    entry={'materialized':{'source_release_id':old}}
    if allowed:
        assert asyncio.run(api.company(SimpleNamespace(),m,cik,entry))['registry_sha256']==registry
    else:
        with pytest.raises(Exception,match='SOURCE_CONTEXT_INVALID'):
            asyncio.run(api.company(SimpleNamespace(),m,cik,entry))
