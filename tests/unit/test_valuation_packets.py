import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / 'deploy/valuation/src'))
from packet_service import serve, PacketError, POINTER


class Bucket:
    def __init__(self, objects):
        self.objects = objects
        self.keys = []

    async def get(self, key):
        self.keys.append(key)
        raw = self.objects.get(key)
        if raw is None:
            return None
        async def text():
            return raw
        return SimpleNamespace(size=len(raw.encode()), text=text)


def fixture():
    raw = json.dumps({'schema_version': 'ppe-valuation-packet-v1', 'ticker': 'MSFT', 'cutoff_date': '2026-01-01'})
    digest = hashlib.sha256(raw.encode()).hexdigest()
    key = f'gold/valuation/releases/{digest}/companies/MSFT.json'
    index = {'schema_version': 'ppe-valuation-index-v1', 'companies': {'MSFT': {'key': key, 'sha256': digest}}}
    return Bucket({POINTER: json.dumps(index), key: raw}), key


def test_compact_packet_reads_only_index_and_company():
    bucket, key = fixture()
    packet, digest = asyncio.run(serve(bucket, 'MSFT', '2026-01-02'))
    assert packet['ticker'] == 'MSFT'
    assert len(digest) == 64
    assert bucket.keys == [POINTER, key]


@pytest.mark.parametrize('failure', ['hash', 'path', 'identity', 'size', 'historical', 'missing'])
def test_fail_closed(failure):
    bucket, key = fixture()
    ticker, cutoff = 'MSFT', '2026-01-02'
    if failure == 'hash':
        bucket.objects[key] += ' '
    elif failure == 'path':
        index = json.loads(bucket.objects[POINTER]);index['companies']['MSFT']['key'] = 'private/data.json';bucket.objects[POINTER] = json.dumps(index)
    elif failure == 'identity':
        payload = json.loads(bucket.objects[key]); payload['ticker'] = 'AAPL'
        raw = json.dumps(payload); digest = hashlib.sha256(raw.encode()).hexdigest()
        new_key = f'gold/valuation/releases/{digest}/companies/MSFT.json'
        bucket.objects[new_key] = raw
        index = json.loads(bucket.objects[POINTER]); index['companies']['MSFT'] = {'key': new_key, 'sha256': digest}
        bucket.objects[POINTER] = json.dumps(index)
    elif failure == 'size':
        bucket.objects[key] = ' ' * 131073
    elif failure == 'historical':
        cutoff = '2025-01-01'
    else:
        ticker = 'AAPL'
    with pytest.raises(PacketError):
        asyncio.run(serve(bucket, ticker, cutoff))
    assert 'private/data.json' not in bucket.keys


class Response:
    def __init__(self, body, status=200, headers=None):
        self.body, self.status, self.headers = body, status, headers or {}

    async def text(self):
        return self.body


class Cache:
    def __init__(self, broken=False):
        self.values, self.writes, self.broken = {}, [], broken

    async def match(self, key):
        if self.broken:
            raise RuntimeError('cache unavailable')
        return self.values.get(key)

    async def put(self, key, response):
        if self.broken:
            raise RuntimeError('cache unavailable')
        self.values[key] = response
        self.writes.append(response.headers['Cache-Control'])


def worker_instance(monkeypatch, cache):
    import importlib.util
    monkeypatch.setitem(sys.modules, 'workers', SimpleNamespace(WorkerEntrypoint=object, Response=Response))
    monkeypatch.setitem(sys.modules, 'js', SimpleNamespace(caches=SimpleNamespace(default=cache),
        Request=SimpleNamespace(new=lambda url: url),
        Response=SimpleNamespace(new=lambda raw, opts: Response(raw, headers=opts['headers'])),
        Object=SimpleNamespace(fromEntries=None)))
    monkeypatch.setitem(sys.modules, 'pyodide.ffi', SimpleNamespace(to_js=lambda value, **kw: value))
    spec = importlib.util.spec_from_file_location('valuation_worker_test', Path(__file__).parents[2] / 'deploy/valuation/src/worker.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    worker = module.Default(); bucket, _ = fixture(); worker.env = SimpleNamespace(VALUATION_DATA=bucket)
    return worker, bucket


@pytest.mark.parametrize('broken', [False, True])
def test_deployed_route_cache_hits_and_cache_failure_fallback(monkeypatch, broken):
    cache = Cache(broken); worker, bucket = worker_instance(monkeypatch, cache)
    request = SimpleNamespace(method='GET', url='https://zion.internal/v1/valuation/company/MSFT?as_of=2026-01-02')
    first = asyncio.run(worker.fetch(request)); second = asyncio.run(worker.fetch(request))
    assert first.status == second.status == 200
    assert json.loads(second.body)['ticker'] == 'MSFT'
    assert len(bucket.keys) == (4 if broken else 2)
    if not broken:
        assert cache.writes == ['public, max-age=60', 'public, max-age=31536000']


@pytest.mark.parametrize('query', ['as_of=2026-01-02&as_of=2026-01-03', 'secret=abc', 'as_of=bad'])
def test_deployed_route_rejects_query_without_reading_storage(monkeypatch, query):
    worker, bucket = worker_instance(monkeypatch, Cache())
    result = asyncio.run(worker.fetch(SimpleNamespace(method='GET', url='https://zion.internal/v1/valuation/company/MSFT?' + query)))
    assert result.status == 400
    assert bucket.keys == []


def test_deployed_route_rejects_cached_packet_with_invalid_hash(monkeypatch):
    cache = Cache(); worker, bucket = worker_instance(monkeypatch, cache)
    request = SimpleNamespace(method='GET', url='https://zion.internal/v1/valuation/company/MSFT?as_of=2026-01-02')
    assert asyncio.run(worker.fetch(request)).status == 200
    immutable_key = next(k for k, v in cache.values.items() if v.headers['Cache-Control'].endswith('31536000'))
    cache.values[immutable_key].body = '{}'
    assert asyncio.run(worker.fetch(request)).status == 200
    assert len(bucket.keys) == 3
